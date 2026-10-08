"""Применение плана: копия снимков, перенос видео, конвертация RAW и HEIC."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.convert import (
    ConversionError,
    Converter,
    detect_heic_converter,
    detect_raw_converter,
    run_converter,
)
from photoarchive.scan import sha256_file


@dataclass
class ApplyReport:
    copied: int = 0
    moved: int = 0
    skipped: int = 0
    converted: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    heic_skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _safe_dest(output: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")):
        raise ValueError(f"путь назначения должен быть относительным: {relative}")
    parts = Path(relative).parts
    if any(part in {"..", ""} for part in parts):
        raise ValueError(f"небезопасный путь назначения: {relative}")
    dest = output.joinpath(*parts)
    if not _is_inside(dest, output):
        raise ValueError(f"путь выходит за выходной каталог: {relative}")
    return dest


def _move_into_archive(src: Path, dest: Path, expected: str | None, report: ApplyReport) -> None:
    """Видео переносится. Уже скопированный ролик удаляется из исходной папки."""
    if not src.is_file():
        if dest.is_file():
            report.skipped += 1
            return
        report.errors.append(f"нет исходного файла: {src}")
        return
    try:
        if src.resolve() == dest.resolve():
            report.skipped += 1
            return
    except OSError:
        pass
    current = sha256_file(src)
    if expected and current != expected:
        report.errors.append(f"исходный файл изменился с момента плана: {src}")
        return
    if dest.exists():
        if sha256_file(dest) == current:
            src.unlink()
            report.moved += 1
            return
        report.conflicts.append(str(dest))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    report.moved += 1


def _copy_bytes(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f".{dest.name}.photoarchive-partial")
    try:
        with src.open("rb") as source, temporary.open("wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        os.replace(temporary, dest)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def apply_plan(
    plan: dict,
    output: Path,
    *,
    raw_converter: Converter | None = None,
    heic_converter: Converter | None = None,
) -> ApplyReport:
    output = output.expanduser().resolve()
    report = ApplyReport()
    inputs = plan.get("inputs") or {}
    for key in ("computer", "google"):
        raw_root = inputs.get(key)
        if raw_root and _is_inside(output, Path(raw_root)):
            raise ValueError("выходной каталог не может лежать внутри исходного")

    if raw_converter is None:
        detected = detect_raw_converter()
        raw_converter = detected[1] if detected else None
    if heic_converter is None:
        detected_heic = detect_heic_converter()
        heic_converter = detected_heic[1] if detected_heic else None

    output.mkdir(parents=True, exist_ok=True)
    for action in plan.get("actions") or []:
        try:
            _apply_action(action, output, report, raw_converter, heic_converter)
        except Exception as exc:  # один сбой не отменяет остальные копии
            report.errors.append(f"{action.get('dest')}: {exc}")
    return report


def _apply_action(
    action: dict,
    output: Path,
    report: ApplyReport,
    raw_converter: Converter | None,
    heic_converter: Converter | None,
) -> None:
    dest = _safe_dest(output, action["dest"])
    src = Path(action["src"])
    operation = action["op"]
    expected = action.get("sha256")

    if operation == "move" or (operation == "copy" and action.get("role") == "video"):
        _move_into_archive(src, dest, expected, report)
        return

    if operation == "copy":
        if not src.is_file():
            report.errors.append(f"нет исходного файла: {src}")
            return
        current = sha256_file(src)
        if expected and current != expected:
            report.errors.append(f"исходный файл изменился с момента плана: {src}")
            return
        if dest.exists():
            if sha256_file(dest) == current:
                report.skipped += 1
                return
            report.conflicts.append(str(dest))
            return
        _copy_bytes(src, dest)
        report.copied += 1
        return

    if operation in {"convert-raw", "convert-heic"}:
        converter = raw_converter if operation == "convert-raw" else heic_converter
        if dest.exists() and dest.stat().st_size > 0:
            report.skipped += 1
            return
        if not src.is_file():
            report.errors.append(f"нет исходного файла: {src}")
            return
        if expected and sha256_file(src) != expected:
            report.errors.append(f"исходный файл изменился с момента плана: {src}")
            return
        if converter is None:
            message = f"нет конвертера для {src}"
            if operation == "convert-heic":
                report.heic_skipped.append(message)
            else:
                report.errors.append(message)
            return
        try:
            run_converter(converter, src, dest)
        except (ConversionError, Exception) as exc:
            message = f"не удалось конвертировать {src}: {exc}"
            if operation == "convert-heic":
                report.heic_skipped.append(message)
            else:
                report.errors.append(message)
            return
        report.converted += 1
        return

    report.errors.append(f"неизвестная операция: {operation}")
