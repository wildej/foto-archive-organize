"""Расширения и типы файлов, которые встречаются в архиве и в Takeout."""

from __future__ import annotations

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".tif",
    ".tiff",
}
HEIC_EXTENSIONS = {".heic", ".heif"}
RAW_EXTENSIONS = {
    ".nef",
    ".nrw",
    ".orf",
    ".dng",
    ".cr2",
    ".cr3",
    ".arw",
    ".rw2",
    ".raf",
    ".pef",
    ".srw",
    ".raw",
    ".rwl",
    ".3fr",
    ".fff",
    ".iiq",
    ".erf",
    ".mef",
    ".mos",
    ".sr2",
    ".x3f",
    ".cap",
    ".rwz",
    ".crw",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".m4v", ".3gp"}
AUDIO_EXTENSIONS = {".wav"}
SIDECAR_EXTENSIONS = {".cos", ".cof", ".cop", ".xmp", ".pp3"}
JUNK_EXTENSIONS = {".lnk", ".tmp", ".doc", ".zip"}
METADATA_EXTENSIONS = {".json"}

# При нескольких RAW одного кадра JPEG собирается из более удобного контейнера.
RAW_PREFERENCE = [
    ".dng",
    ".nef",
    ".nrw",
    ".orf",
    ".cr2",
    ".cr3",
    ".arw",
    ".rw2",
    ".raf",
    ".pef",
]


def kind_for_suffix(suffix: str) -> str:
    ext = suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in HEIC_EXTENSIONS:
        return "heic"
    if ext in RAW_EXTENSIONS:
        return "raw"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in SIDECAR_EXTENSIONS:
        return "sidecar"
    if ext in JUNK_EXTENSIONS:
        return "junk"
    if ext in METADATA_EXTENSIONS:
        return "metadata"
    return "unknown"


def family_for_kind(kind: str) -> str:
    if kind in {"image", "heic"}:
        return "still"
    if kind == "raw":
        return "raw"
    if kind in {"video", "audio"}:
        return "motion"
    return kind
