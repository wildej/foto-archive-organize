"""Дедупликация. У каждого кластера один победитель, без очереди на просмотр.

Точные копии (байты или пиксели) схлопываются.
Чистый ресайз: остаётся большее число пикселей, затем более тяжёлый файл.
Если в кластере есть оригинал и правка, остаётся правка — даже мельче оригинала.
Остальные JPEG кластера в архив не копируются. RAW к этому правилу не относится.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from itertools import combinations
from pathlib import Path

from PIL import Image

from photoarchive.classify import RAW_PREFERENCE, family_for_kind
from photoarchive.metadata import Taken, epoch_seconds
from photoarchive.models import Item, Media, Shot

# Пороги подобраны по синтетическим кадрам: ресайз и повторное сжатие
# почти не отличаются по pHash, кроп и чужой кадр — отличаются сильно.
PHASH_CLOSE = 12
PHASH_RESIZE = 8
PHASH_COLOR = 10
MAD_SAME = 12.0
MAD_COLOR = 18.0
ASPECT_TOL = 0.02
CROP_ASPECT = 0.04
SCALE_MIN = 1.08
TIME_WINDOW_SEC = 2.0


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def add(self, item: int) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


class _BKTree:
    def __init__(self) -> None:
        self.node: list | None = None

    def add(self, value: int, index: int) -> None:
        if self.node is None:
            self.node = [value, [index], {}]
            return
        node = self.node
        while True:
            distance = (value ^ node[0]).bit_count()
            child = node[2].get(distance)
            if child is None:
                if distance == 0:
                    node[1].append(index)
                else:
                    node[2][distance] = [value, [index], {}]
                return
            node = child

    def query(self, value: int, radius: int) -> list[int]:
        if self.node is None:
            return []
        found: list[int] = []
        stack = [self.node]
        while stack:
            node = stack.pop()
            distance = (value ^ node[0]).bit_count()
            if distance <= radius:
                found.extend(node[1])
            for edge, child in node[2].items():
                if abs(edge - distance) <= radius:
                    stack.append(child)
        return found


def _item_from_members(members: list[Media]) -> Item:
    keeper = min(members, key=lambda media: (-media.size, media.explicit_edit, str(media.path)))
    dated = min(members, key=lambda media: (media.taken_rank, str(media.path)))
    stems = {media.stem for media in members if media.stem}
    albums = [(media.album, media.origin) for media in members if media.album]
    sidecars: list[Path] = []
    seen: set[Path] = set()
    for media in members:
        for sidecar in media.sidecars:
            if sidecar not in seen:
                sidecars.append(sidecar)
                seen.add(sidecar)
    donor = keeper if keeper.phash else next((media for media in members if media.phash), keeper)
    return Item(
        kind=keeper.kind,
        path=keeper.path,
        origin=keeper.origin,
        sha256=keeper.sha256,
        size=max(media.size for media in members),
        mtime=max(media.mtime for media in members),
        paths=[media.path for media in sorted(members, key=lambda media: str(media.path))],
        stems=stems,
        stem=keeper.stem,
        width=donor.width,
        height=donor.height,
        phash=donor.phash,
        pixel_hash=donor.pixel_hash or keeper.pixel_hash,
        taken_at=dated.taken_at,
        taken_rank=dated.taken_rank,
        taken_source=dated.taken_source,
        explicit_edit=any(media.explicit_edit for media in members),
        albums=albums,
        sidecars=sidecars,
        ext=keeper.ext,
    )


def collapse_exact(medias: list[Media]) -> tuple[list[Item], int]:
    grouped: dict[tuple[str, str], list[Media]] = defaultdict(list)
    for media in medias:
        grouped[(family_for_kind(media.kind), media.sha256)].append(media)
    items: list[Item] = []
    pixel_owners: dict[str, Item] = {}
    for members in grouped.values():
        item = _item_from_members(members)
        if family_for_kind(item.kind) == "still" and item.pixel_hash:
            previous = pixel_owners.get(item.pixel_hash)
            if previous is not None:
                _absorb(previous, item)
                continue
            pixel_owners[item.pixel_hash] = item
        items.append(item)
    collapsed = len(medias) - len(items)
    return items, collapsed


def _absorb(into: Item, extra: Item) -> None:
    into.paths.extend(extra.paths)
    into.paths = sorted(set(into.paths), key=str)
    into.stems |= extra.stems
    into.albums.extend(extra.albums)
    seen = set(into.sidecars)
    for sidecar in extra.sidecars:
        if sidecar not in seen:
            into.sidecars.append(sidecar)
            seen.add(sidecar)
    into.explicit_edit = into.explicit_edit or extra.explicit_edit
    into.mtime = max(into.mtime, extra.mtime)
    if extra.taken_rank < into.taken_rank:
        into.taken_at = extra.taken_at
        into.taken_rank = extra.taken_rank
        into.taken_source = extra.taken_source
    # При равном размере предпочитаем имя без пометки правки: байты и так одинаковые.
    if extra.size > into.size or (extra.size == into.size and extra.explicit_edit < into.explicit_edit):
        into.path = extra.path
        into.sha256 = extra.sha256
        into.origin = extra.origin
        into.kind = extra.kind
        into.ext = extra.ext
        into.width = extra.width or into.width
        into.height = extra.height or into.height
        into.phash = extra.phash or into.phash
    into.size = max(into.size, extra.size)
    if into.phash is None:
        into.phash = extra.phash
        into.width = into.width or extra.width
        into.height = into.height or extra.height


def _phash_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _hamming(left: str | None, right: str | None) -> int | None:
    a = _phash_int(left)
    b = _phash_int(right)
    if a is None or b is None:
        return None
    return (a ^ b).bit_count()


def _time_close(left: Item, right: Item, window: float = TIME_WINDOW_SEC) -> bool:
    a = left.taken_at
    b = right.taken_at
    if a is None or b is None:
        return False
    if (a.tzinfo is None) == (b.tzinfo is None):
        return abs((a - b).total_seconds()) <= window
    aware = a if a.tzinfo else b
    naive = b if a.tzinfo else a
    local_wall = aware.replace(tzinfo=None)
    utc_wall = aware.astimezone(timezone.utc).replace(tzinfo=None)
    if abs((naive - local_wall).total_seconds()) <= window:
        return True
    return abs((naive - utc_wall).total_seconds()) <= window


def _aspect(item: Item) -> float:
    if not item.width or not item.height:
        return 0.0
    return item.width / item.height


def _aspect_gap(left: Item, right: Item) -> float:
    a, b = _aspect(left), _aspect(right)
    if a <= 0 or b <= 0:
        return 1.0
    return abs(a - b) / max(a, b)


def _simple_scale(left: Item, right: Item) -> bool:
    if not left.width or not left.height or not right.width or not right.height:
        return False
    width_ratio = left.width / right.width
    height_ratio = left.height / right.height
    if width_ratio <= 0 or height_ratio <= 0:
        return False
    if abs(width_ratio - height_ratio) / max(width_ratio, height_ratio) > ASPECT_TOL:
        return False
    factor = max(width_ratio, 1 / width_ratio)
    return factor >= SCALE_MIN


_MAD_CACHE: dict[tuple[str, str], float] = {}


def thumbnail_mad(left: Path, right: Path) -> float:
    key = tuple(sorted((str(left), str(right))))
    cached = _MAD_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(left) as image_a, Image.open(right) as image_b:
            thumb_a = image_a.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS)
            thumb_b = image_b.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS)
            raw_a = thumb_a.tobytes()
            raw_b = thumb_b.tobytes()
    except Exception:
        _MAD_CACHE[key] = 999.0
        return 999.0
    score = sum(abs(a - b) for a, b in zip(raw_a, raw_b)) / len(raw_a)
    _MAD_CACHE[key] = score
    return score


def _visual_relation(left: Item, right: Item, distance: int, linked_by_name_or_time: bool) -> str | None:
    if left.width is None or right.width is None:
        return None
    mad = thumbnail_mad(left.path, right.path)
    same_dims = left.width == right.width and left.height == right.height
    if same_dims and mad <= MAD_SAME and distance <= 6:
        return "recompress"
    if _simple_scale(left, right) and mad <= MAD_SAME and distance <= PHASH_RESIZE:
        return "resize"
    if _aspect_gap(left, right) >= CROP_ASPECT and distance <= PHASH_CLOSE and linked_by_name_or_time:
        return "crop"
    if mad >= MAD_COLOR and distance <= PHASH_COLOR and linked_by_name_or_time:
        return "color"
    if _simple_scale(left, right) and distance <= 4 and mad <= MAD_SAME:
        return "resize"
    return None


def classify_pair(left: Item, right: Item) -> str | None:
    stems_equal = bool(left.stems & right.stems)
    time_close = _time_close(left, right)
    distance = _hamming(left.phash, right.phash)
    explicit = left.explicit_edit or right.explicit_edit
    if explicit and (stems_equal or time_close or (distance is not None and distance <= PHASH_CLOSE)):
        return "edit"
    if distance is None:
        return None
    linked = stems_equal or time_close or distance <= 4
    if not linked:
        return None
    return _visual_relation(left, right, distance, stems_equal or time_close)


def _candidate_pairs(items: list[Item]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    tree = _BKTree()
    hashes: list[int | None] = []
    for index, item in enumerate(items):
        value = _phash_int(item.phash)
        hashes.append(value)
        if value is not None:
            tree.add(value, index)
    for index, value in enumerate(hashes):
        if value is None:
            continue
        for other in tree.query(value, PHASH_CLOSE):
            if other > index:
                pairs.add((index, other))

    by_stem: dict[str, list[int]] = defaultdict(list)
    by_time: dict[int, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        for stem in item.stems:
            by_stem[stem].append(index)
        if item.taken_at is not None:
            stamp = epoch_seconds(item.taken_at)
            by_time[int(stamp // TIME_WINDOW_SEC)].append(index)
            # Окно в 2 секунды может пересечь границу корзины.
            by_time[int((stamp + TIME_WINDOW_SEC) // TIME_WINDOW_SEC)].append(index)

    def add_group(indexes: list[int], wide: bool) -> None:
        unique = sorted(set(indexes))
        if len(unique) < 2:
            return
        if len(unique) > 24 and not wide:
            for left, right in combinations(unique, 2):
                if items[left].explicit_edit or items[right].explicit_edit or _time_close(items[left], items[right]):
                    a, b = (left, right) if left < right else (right, left)
                    pairs.add((a, b))
            return
        if len(unique) > 40:
            return
        for left, right in combinations(unique, 2):
            pairs.add((left, right))

    for indexes in by_stem.values():
        add_group(indexes, wide=False)
    for indexes in by_time.values():
        add_group(indexes, wide=True)
    return pairs


def _crop_candidates(members: list[Item]) -> list[Item]:
    base = max(members, key=lambda item: (item.pixels, item.size, item.sha256))
    return [item for item in members if _aspect_gap(item, base) >= CROP_ASPECT]


def pick_winner(members: list[Item], relations: set[str]) -> Item:
    explicit = [item for item in members if item.explicit_edit]
    if explicit:
        # Среди правок — самая крупная. Оригинал сюда не попадает,
        # даже если у него больше пикселей.
        return max(explicit, key=lambda item: (item.pixels, item.size, item.sha256))
    if "crop" in relations:
        cropped = _crop_candidates(members)
        if cropped:
            return max(cropped, key=lambda item: (item.pixels, item.size, item.sha256))
    if "color" in relations or "edit" in relations:
        return max(members, key=lambda item: (item.mtime, item.size, item.sha256))
    return max(members, key=lambda item: (item.pixels, item.size, item.sha256))


def _cluster_stills(items: list[Item]) -> list[Shot]:
    if not items:
        return []
    union = _UnionFind()
    for index in range(len(items)):
        union.add(index)
    edges: list[tuple[int, int, str]] = []
    for left, right in sorted(_candidate_pairs(items)):
        relation = classify_pair(items[left], items[right])
        if relation is None:
            continue
        union.union(left, right)
        edges.append((left, right, relation))
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        groups[union.find(index)].append(index)
    shots: list[Shot] = []
    for indexes in groups.values():
        member_set = set(indexes)
        members = [items[index] for index in indexes]
        relations = {label for a, b, label in edges if a in member_set and b in member_set}
        edit_signal = None
        if len(members) > 1 and any(item.explicit_edit for item in members):
            edit_signal = "explicit"
        elif "crop" in relations:
            edit_signal = "crop"
        elif "color" in relations:
            edit_signal = "color"
        elif "edit" in relations:
            edit_signal = "edit"
        if edit_signal:
            kind = "edit"
        elif "resize" in relations:
            kind = "resize"
        elif "recompress" in relations:
            kind = "recompress"
        else:
            kind = "single"
        winner = pick_winner(members, relations)
        dropped = [item for item in members if item is not winner]
        # Sidecar проигравших JPEG едет с победителем в _source.
        seen = set(winner.sidecars)
        for item in dropped:
            for sidecar in item.sidecars:
                if sidecar not in seen:
                    winner.sidecars.append(sidecar)
                    seen.add(sidecar)
        shots.append(
            Shot(
                stills=members,
                winner=winner,
                dropped=dropped,
                kind=kind,
                edit_signal=edit_signal,
                raws=[],
            )
        )
    return shots


def _shot_time(shot: Shot) -> Taken | None:
    dated = [
        item
        for item in [*shot.stills, *shot.raws]
        if item.taken_at is not None
    ]
    if not dated:
        return None
    best = min(dated, key=lambda item: (item.taken_rank, str(item.path)))
    return Taken(best.taken_at, best.taken_rank, best.taken_source)


def _time_delta(shot: Shot, raw: Item) -> float:
    if raw.taken_at is None:
        return 10**12
    best = _shot_time(shot)
    if best is None or best.at is None:
        return 10**12
    fake = Item(
        kind="image",
        path=raw.path,
        origin=raw.origin,
        sha256=raw.sha256,
        size=raw.size,
        mtime=raw.mtime,
        paths=raw.paths,
        stems=set(),
        stem="",
        width=None,
        height=None,
        phash=None,
        pixel_hash=None,
        taken_at=best.at,
        taken_rank=best.rank,
        taken_source=best.source,
        explicit_edit=False,
        albums=[],
        sidecars=[],
        ext="",
    )
    if _time_close(fake, raw, window=2):
        return 0.0
    a, b = best.at, raw.taken_at
    if (a.tzinfo is None) != (b.tzinfo is None):
        return 10**9
    return abs((a - b).total_seconds())


def _attach_raws(shots: list[Shot], raws: list[Item]) -> list[Item]:
    stem_map: dict[str, list[Shot]] = defaultdict(list)
    for shot in shots:
        stems: set[str] = set()
        for still in shot.stills:
            stems |= still.stems
        for stem in stems:
            stem_map[stem].append(shot)
    leftover: list[Item] = []
    for raw in raws:
        hits: list[Shot] = []
        seen: set[int] = set()
        for stem in raw.stems:
            for shot in stem_map.get(stem, []):
                marker = id(shot)
                if marker not in seen:
                    hits.append(shot)
                    seen.add(marker)
        if len(hits) == 1:
            hits[0].raws.append(raw)
            continue
        if len(hits) > 1:
            hits.sort(key=lambda shot: (_time_delta(shot, raw), str(shot.winner.path if shot.winner else "")))
            hits[0].raws.append(raw)
            continue
        timed = [shot for shot in shots if _time_delta(shot, raw) == 0.0]
        if len(timed) == 1:
            timed[0].raws.append(raw)
            continue
        leftover.append(raw)
    return leftover


def _group_raw_only(raws: list[Item]) -> list[Shot]:
    if not raws:
        return []
    union = _UnionFind()
    for index in range(len(raws)):
        union.add(index)
    by_stem: dict[str, list[int]] = defaultdict(list)
    for index, raw in enumerate(raws):
        for stem in raw.stems:
            by_stem[stem].append(index)
    for indexes in by_stem.values():
        for other in indexes[1:]:
            union.union(indexes[0], other)
    grouped: dict[int, list[Item]] = defaultdict(list)
    for index, raw in enumerate(raws):
        grouped[union.find(index)].append(raw)
    shots: list[Shot] = []
    for members in grouped.values():
        shots.append(
            Shot(
                stills=[],
                winner=None,
                dropped=[],
                kind="raw-only",
                edit_signal=None,
                raws=members,
                raw_only=True,
            )
        )
    return shots


def preferred_raw(raws: list[Item]) -> Item:
    def sort_key(item: Item) -> tuple:
        try:
            rank = RAW_PREFERENCE.index(item.ext)
        except ValueError:
            rank = len(RAW_PREFERENCE)
        return (rank, -item.size, str(item.path))

    return min(raws, key=sort_key)


def build_shots(medias: list[Media]) -> tuple[list[Shot], list[Item], list[Item], int]:
    """Возвращает кадры, видео, WAV и число точных дублей."""
    _MAD_CACHE.clear()
    items, collapsed = collapse_exact(medias)
    stills = [item for item in items if family_for_kind(item.kind) == "still"]
    raws = [item for item in items if item.kind == "raw"]
    videos = [item for item in items if item.kind == "video"]
    audios = [item for item in items if item.kind == "audio"]
    shots = _cluster_stills(stills)
    leftover = _attach_raws(shots, raws)
    shots.extend(_group_raw_only(leftover))
    return shots, videos, audios, collapsed
