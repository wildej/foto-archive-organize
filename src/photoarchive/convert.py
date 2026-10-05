"""Крючки конвертации RAW → JPEG и HEIC → JPEG.

Рабочий декодер ищется при запуске: rawpy, darktable-cli, dcraw_emu.
Если ничего нет, сухой прогон всё равно строит план, а apply копирует RAW
и сообщает, что JPEG не собран. Тесты подставляют свой конвертер.
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from PIL import Image

Converter = Callable[[Path, Path], None]


class ConversionError(RuntimeError):
    pass


def rawpy_convert(src: Path, dest: Path) -> None:
    import rawpy

    with rawpy.imread(str(src)) as raw:
        rgb = raw.postprocess(use_camera_wb=True)
    Image.fromarray(rgb).save(dest, "JPEG", quality=92)


def darktable_convert(src: Path, dest: Path) -> None:
    subprocess.run(
        ["darktable-cli", str(src), str(dest)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def dcraw_convert(src: Path, dest: Path) -> None:
    completed = subprocess.run(
        ["dcraw_emu", "-c", "-w", str(src)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    image = Image.open(io.BytesIO(completed.stdout))
    image.convert("RGB").save(dest, "JPEG", quality=92)


def detect_raw_converter() -> tuple[str, Converter] | None:
    if importlib.util.find_spec("rawpy") is not None:
        return ("rawpy", rawpy_convert)
    if shutil.which("darktable-cli"):
        return ("darktable-cli", darktable_convert)
    if shutil.which("dcraw_emu"):
        return ("dcraw_emu", dcraw_convert)
    return None


def heic_pillow_convert(src: Path, dest: Path) -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    image = Image.open(src)
    image.convert("RGB").save(dest, "JPEG", quality=92)


def detect_heic_converter() -> tuple[str, Converter] | None:
    if importlib.util.find_spec("pillow_heif") is not None:
        return ("pillow-heif", heic_pillow_convert)
    return None


def command_converter(prefix: list[str]) -> Converter:
    def _run(src: Path, dest: Path) -> None:
        subprocess.run([*prefix, str(src), str(dest)], check=True)

    return _run


def run_converter(converter: Converter, src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".photoarchive-partial")
    try:
        converter(src, temporary)
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise ConversionError(f"конвертер не создал {dest.name}")
        temporary.replace(dest)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
