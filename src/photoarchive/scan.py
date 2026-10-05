"""Обход двух деревьев: хеши, EXIF, JSON Takeout, sidecar."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import exifread
from PIL import Image, ImageOps

from photoarchive.classify import family_for_kind, kind_for_suffix
from photoarchive.metadata import (
    album_from_computer_path,
    album_from_google_path,
    decide_taken,
    exif_tag,
    is_edited_filename,
    load_json,
    metadata_marks_edited,
    normalize_stem,
    parse_photo_taken_timestamp,
)
from photoarchive.models import Media, ScanResult


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_exif(path: Path) -> dict[str, str | None]:
    empty = {"dto": None, "dto_off": None, "dtd": None, "dtd_off": None, "dt": None, "dt_off": None}
    try:
        with path.open("rb") as handle:
            tags = exifread.process_file(
                handle,
                details=False,
                extract_thumbnail=False,
                strict=False,
            )
    except Exception:
        return empty
    if not tags:
        return empty
    return {
        "dto": exif_tag(tags, "DateTimeOriginal"),
        "dto_off": exif_tag(tags, "OffsetTimeOriginal"),
        "dtd": exif_tag(tags, "DateTimeDigitized"),
        "dtd_off": exif_tag(tags, "OffsetTimeDigitized"),
        "dt": exif_tag(tags, "DateTime"),
        "dt_off": exif_tag(tags, "OffsetTime"),
    }


def _still_fingerprint(path: Path) -> tuple[int | None, int | None, str | None, str | None]:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            frame = image.convert("RGB")
            width, height = frame.size
            import imagehash

            perceptual = str(imagehash.phash(frame))
            pixel = hashlib.sha256()
            pixel.update(width.to_bytes(4, "big"))
            pixel.update(height.to_bytes(4, "big"))
            pixel.update(frame.tobytes())
            return width, height, perceptual, pixel.hexdigest()
    except Exception:
        return None, None, None, None


class FileCache:
    def __init__(self, path: Path | None):
        self.conn: sqlite3.Connection | None = None
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_cache (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )

    def get(self, path: Path, size: int, mtime_ns: int) -> dict | None:
        if self.conn is None:
            return None
        row = self.conn.execute(
            "SELECT size, mtime_ns, payload FROM file_cache WHERE path = ?",
            (str(path),),
        ).fetchone()
        if not row or row[0] != size or row[1] != mtime_ns:
            return None
        try:
            return json.loads(row[2])
        except json.JSONDecodeError:
            return None

    def put(self, path: Path, size: int, mtime_ns: int, payload: dict) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            """
            INSERT INTO file_cache(path, size, mtime_ns, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                payload = excluded.payload
            """,
            (str(path), size, mtime_ns, json.dumps(payload, ensure_ascii=False)),
        )

    def close(self) -> None:
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None


def _sidecar_keys(path: Path) -> set[str]:
    """Имена, по которым sidecar узнаёт свой RAW: stem и «stem без последнего суффикса»."""
    keys = {path.stem.casefold(), normalize_stem(path.name)}
    # IMG.NEF.cos → stem IMG.NEF, дополнительный ключ IMG
    if "." in path.stem:
        keys.add(Path(path.stem).stem.casefold())
        keys.add(normalize_stem(path.stem))
    return {key for key in keys if key}


def _claim_sidecars(
    medias: list[Media],
    sidecars: list[Path],
) -> None:
    by_dir: dict[Path, list[Path]] = {}
    for sidecar in sidecars:
        by_dir.setdefault(sidecar.parent, []).append(sidecar)
    claimed: set[Path] = set()
    raws = [media for media in medias if media.kind == "raw"]
    stills = [media for media in medias if media.kind in {"image", "heic"}]
    for owner in [*raws, *stills]:
        for sidecar in by_dir.get(owner.path.parent, []):
            if sidecar in claimed:
                continue
            keys = _sidecar_keys(sidecar)
            names = {owner.path.name.casefold(), owner.path.stem.casefold(), owner.stem}
            if keys & names:
                owner.sidecars.append(sidecar)
                claimed.add(sidecar)


def _attach_json(medias: list[Media], json_files: list[Path], warnings: list[str]) -> None:
    available = {path.name.casefold(): path for path in json_files}
    used: set[Path] = set()
    ordered = sorted(medias, key=lambda media: len(media.path.name), reverse=True)
    for media in ordered:
        candidates = [
            media.path.name + ".json",
            media.path.name + ".supplemental-metadata.json",
            media.path.stem + ".supplemental-metadata.json",
            media.path.stem + ".json",
        ]
        found: Path | None = None
        for candidate in candidates:
            path = available.get(candidate.casefold())
            if path is not None and path not in used and path.parent == media.path.parent:
                found = path
                break
        if found is None:
            continue
        used.add(found)
        payload = load_json(found)
        if payload is None:
            warnings.append(f"не читается JSON: {found}")
            continue
        if metadata_marks_edited(payload) or is_edited_filename(media.path.name):
            media.explicit_edit = True
        google_ts = parse_photo_taken_timestamp(payload)
        # DateTimeOriginal (ранг 0–1) важнее photoTakenTime. Google заменяет
        # только более слабые источники: прочий EXIF, имя файла и mtime.
        if google_ts is None or media.taken_rank <= 1:
            continue
        taken = decide_taken({}, google_ts, media.path.name, None)
        if taken.rank < media.taken_rank:
            media.taken_at = taken.at
            media.taken_rank = taken.rank
            media.taken_source = taken.source


def _fingerprint(path: Path, kind: str, cache: FileCache) -> dict:
    stat = path.stat()
    cached = cache.get(path, stat.st_size, stat.st_mtime_ns)
    if cached is not None:
        return cached
    payload: dict = {
        "sha256": sha256_file(path),
        "width": None,
        "height": None,
        "phash": None,
        "pixel_hash": None,
        "exif": {"dto": None, "dto_off": None, "dtd": None, "dtd_off": None, "dt": None, "dt_off": None},
    }
    if family_for_kind(kind) in {"still", "raw"}:
        payload["exif"] = _read_exif(path)
    if family_for_kind(kind) == "still":
        width, height, perceptual, pixel = _still_fingerprint(path)
        payload["width"] = width
        payload["height"] = height
        payload["phash"] = perceptual
        payload["pixel_hash"] = pixel
    cache.put(path, stat.st_size, stat.st_mtime_ns, payload)
    return payload


def scan_roots(
    computer: Path | None,
    google: Path | None,
    index_path: Path | None = None,
) -> ScanResult:
    cache = FileCache(index_path)
    media: list[Media] = []
    junk: list[dict] = []
    unknown: list[dict] = []
    warnings: list[str] = []
    files_seen = 0
    seen_paths: set[Path] = set()
    try:
        for origin, root in (("computer", computer), ("google", google)):
            if root is None:
                continue
            root = root.expanduser().resolve()
            if not root.is_dir():
                raise FileNotFoundError(f"нет каталога {origin}: {root}")
            json_files: list[Path] = []
            sidecars: list[Path] = []
            batch: list[Media] = []
            def _walk_error(exc: OSError) -> None:
                warnings.append(str(exc))

            for dirpath, dirnames, filenames in root.walk(on_error=_walk_error, follow_symlinks=False):
                dirnames.sort()
                for name in sorted(filenames):
                    path = dirpath / name
                    try:
                        if not path.is_file():
                            continue
                    except OSError as exc:
                        warnings.append(f"{path}: {exc}")
                        continue
                    resolved = path.resolve()
                    files_seen += 1
                    if resolved in seen_paths:
                        continue
                    seen_paths.add(resolved)
                    kind = kind_for_suffix(path.suffix)
                    if kind == "metadata":
                        json_files.append(path)
                        continue
                    if kind == "sidecar":
                        sidecars.append(path)
                        continue
                    if kind == "junk":
                        junk.append({"path": str(path), "ext": path.suffix.lower(), "origin": origin})
                        continue
                    if kind == "unknown":
                        unknown.append({"path": str(path), "ext": path.suffix.lower(), "origin": origin})
                        warnings.append(f"пропущен неизвестный файл: {path}")
                        continue
                    try:
                        stat = path.stat()
                        finger = _fingerprint(path, kind, cache)
                    except OSError as exc:
                        warnings.append(f"{path}: {exc}")
                        continue
                    taken = decide_taken(finger["exif"], None, path.name, stat.st_mtime)
                    if origin == "google":
                        album = album_from_google_path(path, root)
                    else:
                        album = album_from_computer_path(path, root)
                    batch.append(
                        Media(
                            path=path,
                            origin=origin,
                            kind=kind,
                            ext=path.suffix.lower(),
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                            sha256=finger["sha256"],
                            width=finger["width"],
                            height=finger["height"],
                            phash=finger["phash"],
                            pixel_hash=finger["pixel_hash"],
                            taken_at=taken.at,
                            taken_rank=taken.rank,
                            taken_source=taken.source,
                            stem=normalize_stem(path.name),
                            explicit_edit=is_edited_filename(path.name),
                            album=album,
                        )
                    )
            _attach_json(batch, json_files, warnings)
            _claim_sidecars(batch, sidecars)
            media.extend(batch)
    finally:
        cache.close()
    media.sort(key=lambda item: str(item.path))
    return ScanResult(media, junk, unknown, files_seen, warnings)
