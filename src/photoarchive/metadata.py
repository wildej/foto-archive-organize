"""Даты, альбомы и признаки правки."""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_ALBUM = "Разное"
VIDEO_DIRNAME = "Видео"
SOURCE_DIRNAME = "_source"
UNDATED_YEAR = "без-даты"

GOOGLE_MARKERS = {
    "google photos",
    "google фото",
    "google фотографии",
}

_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_MONTH_NUM = re.compile(r"^(?:0[1-9]|1[0-2])$")
_MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "январь", "февраль", "март", "апрель", "май", "июнь", "июль",
    "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
}
_LIBRARY = re.compile(
    r"^(?:"
    r"photos from \d{4}"
    r"|фото из \d{4}(?:\s*г\.?)?"
    r"|фото за \d{4}(?:\s*г\.?)?"
    r"|фотографии из \d{4}(?:\s*г\.?)?"
    r"|фотографии за \d{4}(?:\s*г\.?)?"
    r"|фотографии \d{4}(?:\s*г\.?)?"
    r"|фото \d{4}(?:\s*г\.?)?"
    r")$",
    re.IGNORECASE,
)
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_DEVICE = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "lpt1", "lpt2",
}
_COPY_SUFFIX = re.compile(r"\s*\(\d+\)$")
_EDITED_SUFFIX = re.compile(r"-edited$", re.IGNORECASE)
_FILENAME_DT = re.compile(
    r"(?<!\d)"
    r"(?P<y>19\d{2}|20\d{2})(?P<sep>[-_.]?)"
    r"(?P<m>0[1-9]|1[0-2])(?P=sep)"
    r"(?P<d>0[1-9]|[12]\d|3[01])"
    r"(?:[ T_\-.]?"
    r"(?P<H>[01]\d|2[0-3])(?P<hsep>[-_.:]?)"
    r"(?P<M>[0-5]\d)"
    r"(?:(?P=hsep)(?P<S>[0-5]\d))?"
    r")?"
    r"(?!\d)"
)
_OFFSET = re.compile(
    r"^([+-])(\d{2}):?(\d{2})$|^Z$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Taken:
    at: datetime | None
    rank: int
    source: str


def is_year_bucket(name: str) -> bool:
    return bool(_YEAR.match(name.strip()))


def is_month_bucket(name: str) -> bool:
    text = name.strip().casefold()
    return bool(_MONTH_NUM.match(text) or text in _MONTH_NAMES)


def is_library_bucket(name: str) -> bool:
    text = name.strip()
    return bool(_LIBRARY.match(text) or is_year_bucket(text))


def is_filler_album(name: str) -> bool:
    """Годовой ящик Google и запасное «Разное» — не альбом, отдельную папку не заводят."""
    text = name.strip()
    return text.casefold() == DEFAULT_ALBUM.casefold() or is_library_bucket(text)


def sanitize_folder(name: str) -> str:
    cleaned = _FORBIDDEN.sub("_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned or cleaned.casefold() in _WINDOWS_DEVICE:
        return DEFAULT_ALBUM
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip(" .")
    return cleaned or DEFAULT_ALBUM


def sanitize_filename(name: str) -> str:
    cleaned = _FORBIDDEN.sub("_", name)
    cleaned = cleaned.strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        return "file"
    return cleaned[:180]


def is_edited_filename(name: str) -> bool:
    stem = Path(name).stem
    stem = _COPY_SUFFIX.sub("", stem)
    return bool(_EDITED_SUFFIX.search(stem))


def normalize_stem(name: str) -> str:
    stem = Path(name).stem
    stem = _COPY_SUFFIX.sub("", stem)
    stem = _EDITED_SUFFIX.sub("", stem)
    stem = _COPY_SUFFIX.sub("", stem)
    return stem.casefold()


def metadata_marks_edited(data: object) -> bool:
    """Правка в JSON Takeout: суффикс «-edited» в title или явное поле edited."""
    if not isinstance(data, dict):
        return False
    title = data.get("title")
    if isinstance(title, str) and is_edited_filename(title):
        return True
    stack: list[tuple[object, int]] = [(data, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 4 or not isinstance(current, dict):
            continue
        for key, value in current.items():
            token = str(key).replace("_", "").casefold()
            if token in {"edited", "isedited"} and value is True:
                return True
            if isinstance(value, dict):
                stack.append((value, depth + 1))
    return False


def parse_exif_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("-", ":", 2)
    # «2020:01:15 02:00:00» или с долями секунды
    match = re.match(
        r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})",
        text,
    )
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
        )
    except ValueError:
        return None


def apply_offset(naive: datetime, offset: str | None) -> datetime | None:
    if not offset:
        return None
    text = offset.strip()
    match = _OFFSET.match(text)
    if not match:
        return None
    if text.upper() == "Z":
        return naive.replace(tzinfo=timezone.utc)
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return naive.replace(tzinfo=tz)


def parse_filename_datetime(filename: str) -> datetime | None:
    match = _FILENAME_DT.search(Path(filename).name)
    if not match:
        return None
    try:
        return datetime(
            int(match.group("y")),
            int(match.group("m")),
            int(match.group("d")),
            int(match.group("H") or 0),
            int(match.group("M") or 0),
            int(match.group("S") or 0),
        )
    except ValueError:
        return None


def epoch_seconds(moment: datetime) -> float:
    """Секунды Unix без системного mktime.

    На Windows datetime.timestamp() для даты раньше 1970 бросает
    OSError [Errno 22]. Наивная метка считается UTC.
    """
    if moment.tzinfo is None:
        aware = moment.replace(tzinfo=timezone.utc)
    else:
        offset = moment.utcoffset() or timedelta(0)
        aware = (moment - offset).replace(tzinfo=timezone.utc)
    return calendar.timegm(
        (aware.year, aware.month, aware.day, aware.hour, aware.minute, aware.second)
    ) + aware.microsecond / 1_000_000


def _platform_fromtimestamp(epoch: float, tz: timezone | None) -> datetime:
    if tz is None:
        return datetime.fromtimestamp(epoch)
    return datetime.fromtimestamp(epoch, tz)


def datetime_from_epoch(epoch: float, tz: timezone | None = None) -> datetime | None:
    """Обратное к epoch_seconds. fromtimestamp на Windows тоже даёт Errno 22."""
    try:
        return _platform_fromtimestamp(epoch, tz)
    except (OSError, OverflowError, ValueError):
        try:
            moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=float(epoch))
        except OverflowError:
            return None
        if tz is None:
            return moment.replace(tzinfo=None)
        if tz is timezone.utc:
            return moment
        try:
            return moment.astimezone(tz)
        except (OSError, OverflowError, ValueError):
            return moment


def parse_photo_taken_timestamp(data: object) -> int | None:
    if not isinstance(data, dict):
        return None
    block = data.get("photoTakenTime")
    if not isinstance(block, dict):
        return None
    raw = block.get("timestamp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def decide_taken(
    exif: dict[str, str | None],
    google_ts: int | None,
    filename: str,
    mtime_epoch: float | None,
) -> Taken:
    """Приоритет: DateTimeOriginal, photoTakenTime, прочий EXIF, имя, mtime.

    DateTimeOriginal уже записан местным временем. Если рядом есть OffsetTime,
    календарный день берётся из этих местных часов, а не из UTC photoTakenTime.
    Сам photoTakenTime — момент в UTC, его не сдвигают в пояс машины.
    """
    dto = parse_exif_datetime(exif.get("dto"))
    if dto is not None:
        aware = apply_offset(dto, exif.get("dto_off"))
        if aware is not None:
            return Taken(aware, 0, "exif_datetime_original")
        return Taken(dto, 1, "exif_datetime_original")

    if google_ts is not None:
        instant = datetime_from_epoch(int(google_ts), timezone.utc)
        if instant is not None:
            return Taken(instant, 2, "google_photo_taken_time")

    for source, rank, value_key, offset_key in (
        ("exif_datetime_digitized", 3, "dtd", "dtd_off"),
        ("exif_datetime", 4, "dt", "dt_off"),
    ):
        parsed = parse_exif_datetime(exif.get(value_key))
        if parsed is None:
            continue
        aware = apply_offset(parsed, exif.get(offset_key))
        return Taken(aware or parsed, rank, source)

    named = parse_filename_datetime(filename)
    if named is not None:
        return Taken(named, 5, "filename")

    if mtime_epoch is not None:
        instant = datetime_from_epoch(mtime_epoch)
        if instant is not None:
            return Taken(instant, 6, "mtime")
    return Taken(None, 7, "none")


def folder_parts(taken: Taken) -> tuple[str, str]:
    if taken.at is None:
        return UNDATED_YEAR, "00"
    moment = taken.at
    if taken.source == "google_photo_taken_time":
        moment = moment.astimezone(timezone.utc)
    return f"{moment.year:04d}", f"{moment.month:02d}"


def album_from_google_path(path: Path, root: Path) -> str | None:
    parts = path.relative_to(root).parts[:-1]
    if not parts:
        return None
    lowered = [part.casefold() for part in parts]
    folder: str | None = None
    for index, label in enumerate(lowered):
        if label in GOOGLE_MARKERS and index + 1 < len(parts):
            folder = parts[index + 1]
            break
    if folder is None:
        folder = parts[-1]
    if is_library_bucket(folder) or is_year_bucket(folder) or is_month_bucket(folder):
        return None
    return sanitize_folder(folder)


def album_from_computer_path(path: Path, root: Path) -> str | None:
    parts = path.relative_to(root).parts[:-1]
    if not parts:
        return None
    leaf = parts[-1]
    if is_year_bucket(leaf) or is_month_bucket(leaf) or is_library_bucket(leaf):
        return None
    return sanitize_folder(leaf)


@dataclass(frozen=True)
class AlbumChoice:
    name: str
    origin: str
    conflict: bool
    alternatives: tuple[tuple[str, str], ...]


def choose_album(votes: list[tuple[str, str]]) -> AlbumChoice:
    """votes — пары (имя, computer|google). При споре побеждает альбом Google."""
    unique: dict[tuple[str, str], tuple[str, str]] = {}
    for name, origin in votes:
        if not name:
            continue
        key = (name.casefold(), origin)
        unique.setdefault(key, (name, origin))
    pairs = list(unique.values())
    google = sorted((name for name, origin in pairs if origin == "google"), key=str.casefold)
    computer = sorted((name for name, origin in pairs if origin == "computer"), key=str.casefold)
    folded = {name.casefold() for name, _origin in pairs}
    if not folded:
        return AlbumChoice(DEFAULT_ALBUM, "default", False, ())
    if google:
        chosen = google[0]
        chosen_origin = "google"
    else:
        chosen = computer[0]
        chosen_origin = "computer"
    alternatives = tuple(
        (name, origin)
        for name, origin in sorted(pairs, key=lambda item: (item[1] != "google", item[0].casefold()))
        if name.casefold() != chosen.casefold()
    )
    return AlbumChoice(chosen, chosen_origin, len(folded) > 1, alternatives)


def load_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def exif_tag(tags: dict, leaf: str) -> str | None:
    for key, value in tags.items():
        if str(key).rsplit(" ", 1)[-1] == leaf:
            text = str(value).strip()
            if text:
                return text
    return None
