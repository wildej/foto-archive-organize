"""Сухой прогон: план копирования, без изменений исходников."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from photoarchive.cluster import build_shots, preferred_raw
from photoarchive.convert import detect_heic_converter, detect_raw_converter
from photoarchive.metadata import (
    DEFAULT_ALBUM,
    is_filler_album,
    SOURCE_DIRNAME,
    VIDEO_DIRNAME,
    Taken,
    choose_album,
    folder_parts,
    sanitize_filename,
)
from photoarchive.models import Item, Shot
from photoarchive.scan import scan_roots


def build_plan(
    computer: Path | None = None,
    google: Path | None = None,
    index: Path | None = None,
) -> dict:
    if computer is None and google is None:
        raise ValueError("нужен хотя бы один вход: --computer или --google")
    computer = computer.expanduser().resolve() if computer else None
    google = google.expanduser().resolve() if google else None
    scanned = scan_roots(computer, google, index)
    shots, videos, audios, exact_collapsed = build_shots(scanned.media)

    actions: list[dict] = []
    exact_groups: list[dict] = []
    resizes: list[dict] = []
    edits: list[dict] = []
    conflicts: list[dict] = []
    raw_pairs: list[dict] = []
    raw_only: list[dict] = []
    used: set[str] = set()
    resize_groups = 0
    edit_clusters = 0
    pair_count = 0
    raw_only_count = 0
    heic_to_convert = 0

    def place(relative: str) -> str:
        relative = relative.replace("\\", "/")
        parts = Path(relative).parts
        if any(part in {"..", ""} for part in parts) or relative.startswith("/"):
            raise ValueError(f"небезопасный путь назначения: {relative}")
        candidate = relative
        path = Path(relative)
        number = 2
        while candidate in used:
            candidate = str(path.with_name(f"{path.stem}~{number}{path.suffix}"))
            number += 1
        used.add(candidate)
        return candidate

    def remember_exact(item: Item) -> None:
        if len(item.paths) < 2:
            return
        exact_groups.append(
            {
                "kept": str(item.path),
                "dropped": [str(path) for path in item.paths if path != item.path],
                "reason": "exact",
            }
        )

    def shot_taken(shot: Shot) -> Taken:
        pool = [*shot.stills, *shot.raws]
        dated = [item for item in pool if item.taken_at is not None]
        if not dated:
            return Taken(None, 7, "none")
        best = min(dated, key=lambda item: (item.taken_rank, str(item.path)))
        return Taken(best.taken_at, best.taken_rank, best.taken_source)

    def shot_album(shot: Shot) -> tuple[str, bool]:
        votes: list[tuple[str, str]] = []
        for item in [*shot.stills, *shot.raws]:
            votes.extend(item.albums)
        choice = choose_album(votes)
        if choice.conflict:
            conflicts.append(
                {
                    "chosen": choice.name,
                    "chosen_origin": choice.origin,
                    "alternatives": [
                        {"name": name, "origin": origin} for name, origin in choice.alternatives
                    ],
                    "kept": str(shot.winner.path) if shot.winner else str(shot.raws[0].path),
                }
            )
        return choice.name, choice.conflict

    ordered = sorted(
        shots,
        key=lambda shot: str(shot.winner.path if shot.winner else shot.raws[0].path),
    )
    for shot in ordered:
        for still in shot.stills:
            remember_exact(still)
        for raw in shot.raws:
            remember_exact(raw)
        taken = shot_taken(shot)
        year, month = folder_parts(taken)
        album, _conflict = shot_album(shot)
        base = layout_base(year, month, album)

        if shot.raw_only:
            raw_only_count += 1
            source_raw = preferred_raw(shot.raws)
            jpeg_name = sanitize_filename(f"{source_raw.path.stem}.jpg")
            jpeg_dest = place(f"{base}/{jpeg_name}")
            raw_dests = []
            for raw in sorted(shot.raws, key=lambda item: str(item.path)):
                raw_dest = place(f"{base}/{SOURCE_DIRNAME}/{sanitize_filename(raw.path.name)}")
                actions.append(_copy_action(raw, raw_dest, "raw", taken.source))
                raw_dests.append(raw_dest)
                _queue_sidecars(actions, used, place, raw, base, taken.source)
            actions.append(
                {
                    "op": "convert-raw",
                    "src": str(source_raw.path),
                    "dest": jpeg_dest,
                    "sha256": source_raw.sha256,
                    "role": "raw-jpeg",
                    "date_source": taken.source,
                }
            )
            raw_only.append({"raw": str(source_raw.path), "jpeg_dest": jpeg_dest, "raw_dests": raw_dests})
            continue

        assert shot.winner is not None
        winner = shot.winner
        if shot.kind == "edit" and shot.dropped:
            edit_clusters += 1
            edits.append(
                {
                    "kept": str(winner.path),
                    "dropped": [str(item.path) for item in shot.dropped],
                    "signal": shot.edit_signal,
                }
            )
        elif shot.kind == "resize" and shot.dropped:
            resize_groups += 1
            resizes.append(
                {
                    "kept": str(winner.path),
                    "dropped": [str(item.path) for item in shot.dropped],
                }
            )
        elif shot.kind == "recompress" and shot.dropped:
            exact_collapsed += len(shot.dropped)
            exact_groups.append(
                {
                    "kept": str(winner.path),
                    "dropped": [str(item.path) for item in shot.dropped],
                    "reason": "recompress",
                }
            )

        still_dest = place(f"{base}/{sanitize_filename(winner.path.name)}")
        role = "heic" if winner.kind == "heic" else "still"
        actions.append(_copy_action(winner, still_dest, role, taken.source))
        if winner.kind == "heic":
            heic_to_convert += 1
            jpeg_dest = place(f"{base}/{sanitize_filename(winner.path.stem + '.jpg')}")
            actions.append(
                {
                    "op": "convert-heic",
                    "src": str(winner.path),
                    "dest": jpeg_dest,
                    "sha256": winner.sha256,
                    "role": "heic-jpeg",
                    "date_source": taken.source,
                }
            )
        if winner.sidecars:
            _queue_sidecars(actions, used, place, winner, base, taken.source)
        if shot.raws:
            pair_count += 1
            raw_dests = []
            for raw in sorted(shot.raws, key=lambda item: str(item.path)):
                raw_dest = place(f"{base}/{SOURCE_DIRNAME}/{sanitize_filename(raw.path.name)}")
                actions.append(_copy_action(raw, raw_dest, "raw", taken.source))
                raw_dests.append(raw_dest)
                _queue_sidecars(actions, used, place, raw, base, taken.source)
            raw_pairs.append({"jpeg": still_dest, "raws": [str(raw.path) for raw in shot.raws]})

    for item in sorted(videos, key=lambda media: str(media.path)):
        _queue_motion(actions, conflicts, exact_groups, used, place, item, "video", remember_exact)
    for item in sorted(audios, key=lambda media: str(media.path)):
        _queue_motion(actions, conflicts, exact_groups, used, place, item, "audio", remember_exact)

    warnings = list(scanned.warnings)
    if raw_only_count and detect_raw_converter() is None:
        warnings.append(
            "RAW-конвертер не найден (rawpy, darktable-cli или dcraw_emu). "
            "Apply скопирует RAW в _source и не соберёт JPEG, пока конвертер не установлен "
            "или не передан --raw-converter."
        )
    if heic_to_convert and detect_heic_converter() is None:
        warnings.append(
            "HEIC останется как есть: pillow-heif не установлен, JPEG рядом не собран. "
            "Можно поставить pillow-heif или передать --heic-converter."
        )

    summary = {
        "files_seen": scanned.files_seen,
        "exact_dupes_collapsed": exact_collapsed,
        "resize_groups": resize_groups,
        "edit_clusters": edit_clusters,
        "raw_jpeg_pairs": pair_count,
        "raw_only_conversions": raw_only_count,
        "videos_kept": len(videos),
        "audio_kept": len(audios),
        "junk_skipped": len(scanned.junk),
        "unknown_skipped": len(scanned.unknown),
        "album_conflicts": len(conflicts),
        "heic_to_convert": heic_to_convert,
    }
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run",
        "inputs": {
            "computer": str(computer) if computer else None,
            "google": str(google) if google else None,
        },
        "policy": {
            "edits": "keep-edit",
            "video_dirname": VIDEO_DIRNAME,
            "source_dirname": SOURCE_DIRNAME,
            "default_album": DEFAULT_ALBUM,
        },
        "summary": summary,
        "actions": actions,
        "exact_groups": exact_groups,
        "resizes": resizes,
        "edits": edits,
        "raw_pairs": raw_pairs,
        "raw_only": raw_only,
        "album_conflicts": conflicts,
        "junk": scanned.junk,
        "unknown": scanned.unknown,
        "warnings": warnings,
    }


def layout_base(year: str, month: str, album: str) -> str:
    if is_filler_album(album):
        return f"{year}/{month}"
    return f"{year}/{month}/{album}"


def _copy_action(item: Item, dest: str, role: str, date_source: str) -> dict:
    return {
        "op": "copy",
        "src": str(item.path),
        "dest": dest,
        "sha256": item.sha256,
        "role": role,
        "date_source": date_source,
    }


def _queue_sidecars(actions, used, place, item: Item, base: str, date_source: str) -> None:
    del used  # place() уже держит занятые пути
    for sidecar in item.sidecars:
        dest = place(f"{base}/{SOURCE_DIRNAME}/{sanitize_filename(sidecar.name)}")
        actions.append(
            {
                "op": "copy",
                "src": str(sidecar),
                "dest": dest,
                "sha256": _sha_later(sidecar),
                "role": "sidecar",
                "date_source": date_source,
            }
        )


def _sha_later(path: Path) -> str:
    from photoarchive.scan import sha256_file

    return sha256_file(path)


def _queue_motion(actions, conflicts, exact_groups, used, place, item: Item, role: str, remember_exact) -> None:
    del used, exact_groups
    remember_exact(item)
    taken = Taken(item.taken_at, item.taken_rank, item.taken_source)
    year, month = folder_parts(taken)
    choice = choose_album(list(item.albums))
    if choice.conflict:
        conflicts.append(
            {
                "chosen": choice.name,
                "chosen_origin": choice.origin,
                "alternatives": [
                    {"name": name, "origin": origin} for name, origin in choice.alternatives
                ],
                "kept": str(item.path),
            }
        )
    dest = place(
        f"{layout_base(year, month, choice.name)}/{VIDEO_DIRNAME}/{sanitize_filename(item.path.name)}"
    )
    action = _copy_action(item, dest, role, taken.source)
    if role == "video":
        action["op"] = "move"
    actions.append(action)


def write_plan(plan: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
