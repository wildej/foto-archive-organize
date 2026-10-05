"""Маленькие синтетические файлы для тестов. Это не настоящий архив."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image


def patterned(size: tuple[int, int], seed: int) -> Image.Image:
    image = Image.new("RGB", size)
    pixels = image.load()
    rng = random.Random(seed)
    width, height = size
    for top in range(0, height, 8):
        for left in range(0, width, 8):
            color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
            for y in range(top, min(top + 8, height)):
                for x in range(left, min(left + 8, width)):
                    pixels[x, y] = color
    return image


def write_jpeg(
    path: Path,
    size: tuple[int, int] = (64, 64),
    seed: int = 1,
    dto: str | None = None,
    offset: str | None = None,
    quality: int = 90,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = patterned(size, seed)
    kwargs = {"quality": quality}
    if dto:
        exif = Image.Exif()
        exif[0x9003] = dto
        if offset:
            exif[0x9011] = offset
        kwargs["exif"] = exif
    image.save(path, "JPEG", **kwargs)
    return path


def write_resized(path: Path, source: Path, size: tuple[int, int], quality: int = 90) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        resized = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        resized.save(path, "JPEG", quality=quality)
    return path


def shift_color(path: Path, source: Path, dto: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        frame = image.convert("RGB")
        pixels = frame.load()
        width, height = frame.size
        for y in range(height):
            for x in range(width):
                red, green, blue = pixels[x, y]
                pixels[x, y] = (min(255, red + 80), green, max(0, blue - 40))
        kwargs = {"quality": 90}
        if dto:
            exif = Image.Exif()
            exif[0x9003] = dto
            kwargs["exif"] = exif
        frame.save(path, "JPEG", **kwargs)
    return path
