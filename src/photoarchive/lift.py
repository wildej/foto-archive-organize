"""Поднять файлы из годовых ящиков в год/месяц. Исходный архив не трогает."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.metadata import is_filler_album
from photoarchive.scan import sha256_file


@dataclass
class LiftReport:
    folders: int = 0
    moved: int = 0
    merged: int = 0
    removed_dupes: int = 0
    renamed: int = 0
    errors: list[str] = field(default_factory=list)


def lift_filler(root: Path) -> LiftReport:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"нет каталога: {root}")
    report = LiftReport()
    for year in sorted(path for path in root.iterdir() if path.is_dir()):
        for month in sorted(path for path in year.iterdir() if path.is_dir()):
            for album in sorted(path for path in month.iterdir() if path.is_dir()):
                if not is_filler_album(album.name):
                    continue
                _lift_dir(album, month, report)
                if album.exists() and not any(album.iterdir()):
                    album.rmdir()
                    report.folders += 1
    return report


def _lift_dir(source: Path, dest_dir: Path, report: LiftReport) -> None:
    for child in sorted(source.iterdir(), key=lambda path: path.name.casefold()):
        target = dest_dir / child.name
        try:
            if child.is_dir():
                if target.exists() and target.is_dir():
                    _lift_dir(child, target, report)
                    if child.exists() and not any(child.iterdir()):
                        child.rmdir()
                    report.merged += 1
                    continue
                if target.exists():
                    target = _unique(dest_dir, child.name)
                    report.renamed += 1
                child.rename(target)
                report.moved += 1
                continue
            if target.exists() and target.is_file():
                if _same_file(child, target):
                    child.unlink()
                    report.removed_dupes += 1
                    continue
                target = _unique(dest_dir, child.name)
                report.renamed += 1
            elif target.exists():
                target = _unique(dest_dir, child.name)
                report.renamed += 1
            child.rename(target)
            report.moved += 1
        except OSError as exc:
            report.errors.append(f"{child}: {exc}")


def _same_file(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    return sha256_file(left) == sha256_file(right)


def _unique(directory: Path, name: str) -> Path:
    path = Path(name)
    stem, suffix = path.stem, path.suffix
    number = 2
    while True:
        candidate = directory / f"{stem} ({number}){suffix}"
        if not candidate.exists():
            return candidate
        number += 1


def format_lift_report(report: LiftReport) -> str:
    lines = [
        f"Папок убрано: {report.folders}",
        f"Перенесено: {report.moved}",
        f"Слито каталогов: {report.merged}",
        f"Повторных файлов удалено: {report.removed_dupes}",
        f"Переименовано из-за конфликта: {report.renamed}",
        f"Ошибок: {len(report.errors)}",
    ]
    lines.extend(f"- ошибка: {item}" for item in report.errors)
    return "\n".join(lines)
