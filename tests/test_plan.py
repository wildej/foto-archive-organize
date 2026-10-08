import json
import os
from datetime import datetime, timezone
from pathlib import Path

from photoarchive.plan import build_plan
from tests.helpers import shift_color, write_jpeg, write_resized


DTO = "2019:06:15 14:30:00"


def _dests(plan: dict, role: str | None = None) -> list[str]:
    actions = plan["actions"]
    if role is not None:
        actions = [action for action in actions if action["role"] == role]
    return [action["dest"] for action in actions]


def _srcs(plan: dict, role: str | None = None) -> list[str]:
    actions = plan["actions"]
    if role is not None:
        actions = [action for action in actions if action["role"] == role]
    return [action["src"] for action in actions]


def test_plan_json_has_no_review_gate(tmp_path: Path):
    folder = tmp_path / "computer" / "Альбом"
    write_jpeg(folder / "solo.jpg", seed=1, dto=DTO)
    plan = build_plan(folder.parent)
    blob = json.dumps(plan)
    assert "needs_review" not in blob
    assert "needs-review" not in blob
    assert plan["policy"]["edits"] == "keep-edit"
    assert plan["mode"] == "dry-run"
    assert plan["summary"]["edit_clusters"] == 0


def test_duplicates_collapse_inside_each_tree_and_not_across_different_shots(tmp_path: Path):
    """Две одинаковые JPEG только в компьютере и две другие только в Takeout."""
    computer = tmp_path / "computer"
    google_root = tmp_path / "google"
    takeout = google_root / "Takeout" / "Google Photos"
    computer_one = write_jpeg(computer / "Папка" / "a.jpg", size=(40, 40), seed=31, dto=DTO)
    computer_copy = computer / "Копия" / "a-copy.jpg"
    computer_copy.parent.mkdir(parents=True)
    computer_copy.write_bytes(computer_one.read_bytes())
    takeout_one = write_jpeg(takeout / "Альбом" / "g.jpg", size=(40, 40), seed=32, dto="2020:03:04 05:06:07")
    takeout_copy = takeout / "Другой" / "g-copy.jpg"
    takeout_copy.parent.mkdir(parents=True)
    takeout_copy.write_bytes(takeout_one.read_bytes())
    plan = build_plan(computer, google_root)
    assert plan["summary"]["exact_dupes_collapsed"] == 2
    assert len(_srcs(plan, "still")) == 2
    assert len(plan["exact_groups"]) == 2
    kept = {Path(src).name for src in _srcs(plan, "still")}
    assert len(kept) == 2
    dropped = {Path(path).name for group in plan["exact_groups"] for path in group["dropped"]}
    assert kept.isdisjoint(dropped)
    assert kept | dropped == {"a.jpg", "a-copy.jpg", "g.jpg", "g-copy.jpg"}


def test_exact_duplicate_collapses(tmp_path: Path):
    root = tmp_path / "computer"
    original = write_jpeg(root / "Альбом" / "same.jpg", size=(48, 48), seed=3, dto=DTO)
    other = root / "Другое" / "same.jpg"
    other.parent.mkdir()
    other.write_bytes(original.read_bytes())
    plan = build_plan(root)
    assert plan["summary"]["exact_dupes_collapsed"] == 1
    assert len(_srcs(plan, "still")) == 1
    assert plan["summary"]["album_conflicts"] == 1
    assert plan["album_conflicts"][0]["chosen"] == "Альбом" or plan["album_conflicts"][0]["chosen"] == "Другое"


def test_google_album_wins_conflict(tmp_path: Path):
    picture = write_jpeg(tmp_path / "seed.jpg", size=(40, 40), seed=4, dto="2018:04:09 10:00:00")
    computer = tmp_path / "computer" / "Отпуск" / "a.jpg"
    google = tmp_path / "google" / "Takeout" / "Google Photos" / "Италия" / "a.jpg"
    computer.parent.mkdir(parents=True)
    google.parent.mkdir(parents=True)
    computer.write_bytes(picture.read_bytes())
    google.write_bytes(picture.read_bytes())
    plan = build_plan(tmp_path / "computer", tmp_path / "google")
    assert plan["summary"]["album_conflicts"] == 1
    assert plan["album_conflicts"][0]["chosen"] == "Италия"
    assert plan["album_conflicts"][0]["chosen_origin"] == "google"
    assert _dests(plan, "still") == ["2018/04/Италия/a.jpg"]


def test_resize_keeps_largest(tmp_path: Path):
    root = tmp_path / "computer" / "Кадр"
    big = write_jpeg(root / "pic.jpg", size=(128, 128), seed=5, dto=DTO)
    write_resized(root / "pic-small.jpg", big, (64, 64))
    plan = build_plan(root.parent)
    assert plan["summary"]["resize_groups"] == 1
    assert plan["summary"]["edit_clusters"] == 0
    assert [Path(src).name for src in _srcs(plan, "still")] == ["pic.jpg"]


def test_edited_file_wins_even_if_smaller(tmp_path: Path):
    root = tmp_path / "computer" / "Альбом"
    original = write_jpeg(root / "shot.jpg", size=(80, 80), seed=6, dto=DTO)
    edited = write_jpeg(root / "shot-edited.jpg", size=(20, 20), seed=7, dto=DTO)
    assert edited.stat().st_size < original.stat().st_size
    plan = build_plan(root.parent)
    assert plan["summary"]["edit_clusters"] == 1
    assert plan["summary"]["resize_groups"] == 0
    assert Path(_srcs(plan, "still")[0]).name == "shot-edited.jpg"
    assert plan["edits"][0]["signal"] == "explicit"
    assert Path(plan["edits"][0]["dropped"][0]).name == "shot.jpg"
    blob = json.dumps(plan)
    assert "needs_review" not in blob
    assert "needs-review" not in blob


def test_takeout_edited_flag_wins(tmp_path: Path):
    root = tmp_path / "google" / "Google Photos" / "Море"
    write_jpeg(root / "frame.jpg", size=(64, 64), seed=8, dto=DTO)
    grade = root / "frame-grade.jpg"
    write_jpeg(grade, size=(24, 24), seed=9, dto=DTO)
    (root / "frame-grade.jpg.json").write_text(
        json.dumps({"title": "frame-grade.jpg", "edited": True, "photoTakenTime": {"timestamp": "1560609000"}}),
        encoding="utf-8",
    )
    plan = build_plan(google=root.parents[2])
    assert plan["summary"]["edit_clusters"] == 1
    assert Path(_srcs(plan, "still")[0]).name == "frame-grade.jpg"
    assert not any(action["role"] == "still" and action["src"].endswith("frame.jpg") for action in plan["actions"])


def test_raw_and_jpeg_pair_and_edited_keeps_raw(tmp_path: Path):
    root = tmp_path / "computer" / "Съёмка"
    write_jpeg(root / "DSC_1000.JPG", size=(64, 64), seed=10, dto=DTO)
    write_jpeg(root / "DSC_1000-edited.JPG", size=(24, 24), seed=11, dto=DTO)
    (root / "DSC_1000.NEF").write_bytes(b"NEF-fake-bytes")
    (root / "DSC_1000.NEF.xmp").write_text("<xmp/>", encoding="utf-8")
    (root / "DSC_1000.cos").write_text("cos", encoding="utf-8")
    plan = build_plan(root.parent)
    assert plan["summary"]["raw_jpeg_pairs"] == 1
    assert plan["summary"]["raw_only_conversions"] == 0
    assert plan["summary"]["edit_clusters"] == 1
    stills = _srcs(plan, "still")
    assert len(stills) == 1 and stills[0].endswith("DSC_1000-edited.JPG")
    assert any(dest.endswith("_source/DSC_1000.NEF") for dest in _dests(plan, "raw"))
    sidecar_dests = _dests(plan, "sidecar")
    assert any(dest.endswith("_source/DSC_1000.NEF.xmp") for dest in sidecar_dests)
    assert any(dest.endswith("_source/DSC_1000.cos") for dest in sidecar_dests)
    assert not any(src.endswith("DSC_1000.JPG") and "edited" not in src for src in _srcs(plan))


def test_raw_only_schedules_conversion(tmp_path: Path):
    root = tmp_path / "computer" / "Съёмка"
    root.mkdir(parents=True)
    (root / "DSC_2000.NEF").write_bytes(b"RAW-ONLY")
    plan = build_plan(root.parent)
    assert plan["summary"]["raw_only_conversions"] == 1
    assert any(action["op"] == "convert-raw" and action["dest"].endswith("DSC_2000.jpg") for action in plan["actions"])
    assert any(action["role"] == "raw" and "_source" in action["dest"] for action in plan["actions"])
    assert not any(action["role"] == "still" for action in plan["actions"])


def test_video_and_wav_go_to_video_folder(tmp_path: Path):
    root = tmp_path / "computer" / "Поездка"
    write_jpeg(root / "pic.jpg", size=(32, 32), seed=12, dto=DTO)
    (root / "VID_20190615_120000.mp4").write_bytes(b"mp4")
    (root / "SND_20190615_120000.wav").write_bytes(b"wav")
    (root / "clip.MOV").write_bytes(b"mov")
    plan = build_plan(root.parent)
    assert plan["summary"]["videos_kept"] == 2
    assert plan["summary"]["audio_kept"] == 1
    jpeg_dest = _dests(plan, "still")[0]
    assert jpeg_dest == "2019/06/Поездка/pic.jpg"
    assert "/Видео/" not in jpeg_dest
    assert all(action["op"] == "move" for action in plan["actions"] if action["role"] == "video")
    assert all(action["op"] == "copy" for action in plan["actions"] if action["role"] == "audio")
    video_dests = _dests(plan, "video")
    assert any(dest == "2019/06/Поездка/Видео/VID_20190615_120000.mp4" for dest in video_dests)
    assert any(dest.endswith("/Видео/clip.MOV") for dest in video_dests)
    assert _dests(plan, "audio") == ["2019/06/Поездка/Видео/SND_20190615_120000.wav"]


def test_junk_skipped_and_default_album(tmp_path: Path):
    root = tmp_path / "computer" / "2019" / "06"
    write_jpeg(root / "loose.jpg", size=(32, 32), seed=13, dto=DTO)
    for name in ("a.lnk", "b.tmp", "c.doc", "d.zip"):
        (root / name).write_bytes(b"junk")
    (root / "odd.xyz").write_bytes(b"???")
    plan = build_plan(tmp_path / "computer")
    assert plan["summary"]["junk_skipped"] == 4
    assert plan["summary"]["unknown_skipped"] == 1
    assert _dests(plan, "still") == ["2019/06/Разное/loose.jpg"]
    assert not any(action["src"].endswith(".lnk") for action in plan["actions"])


def test_google_json_date_and_album(tmp_path: Path):
    root = tmp_path / "google" / "Takeout" / "Google Photos" / "Корсика"
    root.mkdir(parents=True)
    write_jpeg(root / "pic.jpg", size=(32, 32), seed=14)
    stamp = int(datetime(2020, 1, 31, 12, 0, tzinfo=timezone.utc).timestamp())
    (root / "pic.jpg.json").write_text(
        json.dumps({"title": "pic.jpg", "photoTakenTime": {"timestamp": str(stamp)}}),
        encoding="utf-8",
    )
    # Имя без даты, чтобы не перебить JSON. Пояс машины не должен сдвинуть январь в февраль.
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC-14"
    try:
        import time

        time.tzset()
        plan = build_plan(google=tmp_path / "google")
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        import time

        time.tzset()
    assert _dests(plan, "still") == ["2020/01/Корсика/pic.jpg"]
    assert plan["actions"][0]["date_source"] == "google_photo_taken_time"
    assert not any(action["src"].endswith(".json") for action in plan["actions"])


def test_filename_date_beats_mtime(tmp_path: Path):
    root = tmp_path / "computer" / "Альбом"
    path = write_jpeg(root / "IMG_20181103_120000.jpg", size=(32, 32), seed=15)
    os.utime(path, (1_800_000_000, 1_800_000_000))
    plan = build_plan(root.parent)
    assert _dests(plan, "still") == ["2018/11/Альбом/IMG_20181103_120000.jpg"]
    assert plan["actions"][0]["date_source"] == "filename"


def test_different_photos_same_second_stay_apart(tmp_path: Path):
    root = tmp_path / "computer" / "Альбом"
    write_jpeg(root / "one.jpg", size=(64, 64), seed=1, dto=DTO)
    write_jpeg(root / "two.jpg", size=(64, 64), seed=2, dto=DTO)
    plan = build_plan(root.parent)
    assert plan["summary"]["edit_clusters"] == 0
    assert plan["summary"]["resize_groups"] == 0
    assert plan["summary"]["exact_dupes_collapsed"] == 0
    assert len(_srcs(plan, "still")) == 2


def test_color_sibling_keeps_newer_file(tmp_path: Path):
    root = tmp_path / "computer" / "Альбом"
    original = write_jpeg(root / "grade.jpg", size=(64, 64), seed=16, dto=DTO)
    edited = shift_color(root / "grade-look.jpg", original, dto=DTO)
    os.utime(original, (1_000, 1_000))
    os.utime(edited, (2_000, 2_000))
    plan = build_plan(root.parent)
    assert plan["summary"]["edit_clusters"] == 1
    assert Path(_srcs(plan, "still")[0]).name == "grade-look.jpg"


def test_library_bucket_is_not_an_album(tmp_path: Path):
    root = tmp_path / "google" / "Google Photos" / "Photos from 2019"
    write_jpeg(root / "pic.jpg", size=(32, 32), seed=17, dto=DTO)
    plan = build_plan(google=tmp_path / "google")
    assert _dests(plan, "still") == ["2019/06/Разное/pic.jpg"]
