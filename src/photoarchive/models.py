"""Записи сканирования и сгруппированный кадр."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Media:
    path: Path
    origin: str
    kind: str
    ext: str
    size: int
    mtime: float
    sha256: str
    width: int | None = None
    height: int | None = None
    phash: str | None = None
    pixel_hash: str | None = None
    taken_at: datetime | None = None
    taken_rank: int = 99
    taken_source: str = "none"
    stem: str = ""
    explicit_edit: bool = False
    album: str | None = None
    sidecars: list[Path] = field(default_factory=list)


@dataclass
class Item:
    """Один логический файл после схлопывания точных копий."""

    kind: str
    path: Path
    origin: str
    sha256: str
    size: int
    mtime: float
    paths: list[Path]
    stems: set[str]
    stem: str
    width: int | None
    height: int | None
    phash: str | None
    pixel_hash: str | None
    taken_at: datetime | None
    taken_rank: int
    taken_source: str
    explicit_edit: bool
    albums: list[tuple[str, str]]
    sidecars: list[Path]
    ext: str

    @property
    def pixels(self) -> int:
        if self.width and self.height:
            return self.width * self.height
        return 0


@dataclass
class Shot:
    stills: list[Item]
    winner: Item | None
    dropped: list[Item]
    kind: str
    edit_signal: str | None
    raws: list[Item]
    raw_only: bool = False


@dataclass
class ScanResult:
    media: list[Media]
    junk: list[dict]
    unknown: list[dict]
    files_seen: int
    warnings: list[str]
