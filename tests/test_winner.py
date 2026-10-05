from pathlib import Path

from photoarchive.cluster import pick_winner
from photoarchive.models import Item


def _item(name: str, width: int, height: int, *, edited: bool = False, size: int = 100, mtime: float = 1) -> Item:
    return Item(
        kind="image",
        path=Path(name),
        origin="computer",
        sha256=name,
        size=size,
        mtime=mtime,
        paths=[Path(name)],
        stems={Path(name).stem},
        stem=Path(name).stem,
        width=width,
        height=height,
        phash=None,
        pixel_hash=None,
        taken_at=None,
        taken_rank=6,
        taken_source="mtime",
        explicit_edit=edited,
        albums=[],
        sidecars=[],
        ext=".jpg",
    )


def test_lower_resolution_edit_beats_original():
    original = _item("shot.jpg", 400, 300, size=5000)
    edited = _item("shot-edited.jpg", 80, 60, edited=True, size=800)
    winner = pick_winner([original, edited], {"edit"})
    assert winner is edited


def test_resize_without_edit_keeps_largest_pixels_then_bytes():
    small = _item("a.jpg", 100, 80, size=9000)
    large_light = _item("b.jpg", 200, 160, size=1000)
    large_heavy = _item("c.jpg", 200, 160, size=4000)
    winner = pick_winner([small, large_light, large_heavy], {"resize"})
    assert winner is large_heavy


def test_crop_keeps_cropped_frame_not_the_full_one():
    full = _item("full.jpg", 400, 300, size=8000)
    crop = _item("crop.jpg", 200, 120, size=2000)
    winner = pick_winner([full, crop], {"crop"})
    assert winner is crop
