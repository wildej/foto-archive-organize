import json
import os
from pathlib import Path

from PIL import Image

from photoarchive.apply import apply_plan
from photoarchive.plan import build_plan
from photoarchive.scan import sha256_file
from tests.helpers import write_jpeg


def _converter(src: Path, dest: Path) -> None:
    del src
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (12, 34, 56)).save(dest, "JPEG", quality=90)


def test_apply_is_idempotent_and_leaves_sources(tmp_path: Path):
    root = tmp_path / "computer" / "Альбом"
    source = write_jpeg(root / "pic.jpg", size=(32, 32), seed=21, dto="2019:06:15 14:30:00")
    (root / "note.lnk").write_bytes(b"nope")
    before = (source.stat().st_mtime_ns, sha256_file(source))
    plan = build_plan(root.parent)
    after_plan = (source.stat().st_mtime_ns, sha256_file(source))
    assert after_plan == before
    output = tmp_path / "out"
    assert not output.exists()
    first = apply_plan(plan, output)
    assert first.copied == 1
    assert first.errors == []
    copied = output / "2019" / "06" / "Альбом" / "pic.jpg"
    assert copied.read_bytes() == source.read_bytes()
    assert not (output / "2019" / "06" / "Альбом" / "note.lnk").exists()
    assert (source.stat().st_mtime_ns, sha256_file(source)) == before
    second = apply_plan(plan, output)
    assert second.copied == 0
    assert second.skipped == 1
    assert second.errors == []


def test_raw_only_uses_converter_hook(tmp_path: Path):
    root = tmp_path / "computer" / "Съёмка"
    root.mkdir(parents=True)
    raw = root / "DSC_9.NEF"
    raw.write_bytes(b"not-a-real-raw")
    (root / "DSC_9.xmp").write_text("sidecar", encoding="utf-8")
    before = raw.read_bytes()
    plan = build_plan(root.parent)
    output = tmp_path / "out"
    report = apply_plan(plan, output, raw_converter=_converter)
    assert report.errors == []
    assert report.converted == 1
    # У фейкового RAW нет даты в EXIF и в имени, поэтому месяц берётся из mtime.
    jpegs = list(output.rglob("DSC_9.jpg"))
    assert len(jpegs) == 1
    assert jpegs[0].parent.name == "Съёмка"
    assert jpegs[0].parent.name != "_source"
    raws = list(output.rglob("DSC_9.NEF"))
    assert len(raws) == 1 and raws[0].parent.name == "_source"
    sidecars = list(output.rglob("DSC_9.xmp"))
    assert len(sidecars) == 1 and sidecars[0].parent.name == "_source"
    assert raw.read_bytes() == before
    again = apply_plan(plan, output, raw_converter=_converter)
    assert again.converted == 0
    assert again.copied == 0
    assert again.skipped >= 2
    assert jpegs[0].exists()


def test_missing_raw_converter_still_copies_raw(tmp_path: Path):
    root = tmp_path / "computer" / "Съёмка"
    root.mkdir(parents=True)
    (root / "ONLY.ORF").write_bytes(b"orf")
    plan = build_plan(root.parent)
    report = apply_plan(plan, tmp_path / "out", raw_converter=None, heic_converter=None)
    # detect_raw_converter может найти системный декодер; тогда ошибки нет.
    # Если декодера нет, RAW всё равно должен лежать в _source, а исходник — на месте.
    assert (root / "ONLY.ORF").read_bytes() == b"orf"
    copied = list((tmp_path / "out").rglob("ONLY.ORF"))
    assert len(copied) == 1 and copied[0].parent.name == "_source"
    if report.converted == 0:
        assert report.errors


def test_video_is_moved_even_from_old_copy_plan(tmp_path: Path):
    root = tmp_path / "computer" / "Поездка"
    root.mkdir(parents=True)
    source = root / "VID_20190615_120000.mp4"
    source.write_bytes(b"video-bytes")
    plan = build_plan(root.parent)
    video = next(action for action in plan["actions"] if action["role"] == "video")
    assert video["op"] == "move"
    video["op"] = "copy"
    output = tmp_path / "out"
    first = apply_plan(plan, output)
    dest = output / "2019" / "06" / "Поездка" / "Видео" / "VID_20190615_120000.mp4"
    assert first.moved == 1
    assert first.copied == 0
    assert dest.read_bytes() == b"video-bytes"
    assert not source.exists()
    source.write_bytes(b"video-bytes")
    placed = apply_plan(plan, output)
    assert placed.moved == 1
    assert not source.exists()
    assert dest.read_bytes() == b"video-bytes"
    third = apply_plan(plan, output)
    assert third.moved == 0
    assert third.skipped == 1
    assert third.errors == []


def test_output_inside_source_is_refused(tmp_path: Path):
    root = tmp_path / "computer"
    write_jpeg(root / "a.jpg", seed=1, dto="2019:01:02 03:04:05")
    plan = build_plan(root)
    try:
        apply_plan(plan, root / "nested")
    except ValueError as exc:
        assert "внутри" in str(exc)
    else:
        raise AssertionError("ожидался отказ")


def test_heic_kept_and_optional_jpeg(tmp_path: Path):
    root = tmp_path / "computer" / "Альбом"
    root.mkdir(parents=True)
    heic = root / "pic.heic"
    heic.write_bytes(b"not-really-heic")
    plan = build_plan(root.parent)
    assert any(action["role"] == "heic" for action in plan["actions"])
    assert any(action["op"] == "convert-heic" for action in plan["actions"])
    output = tmp_path / "out"
    report = apply_plan(plan, output, heic_converter=_converter, raw_converter=_converter)
    assert report.errors == []
    assert list(output.rglob("pic.heic"))
    assert list(output.rglob("pic.jpg"))
    bare = apply_plan(plan, tmp_path / "out2", heic_converter=None, raw_converter=None)
    assert list((tmp_path / "out2").rglob("pic.heic"))
    if bare.converted == 0:
        assert bare.heic_skipped
        assert bare.errors == []


def test_cli_dry_run(tmp_path: Path):
    import subprocess
    import sys

    root = tmp_path / "computer" / "Альбом"
    write_jpeg(root / "pic.jpg", size=(24, 24), seed=22, dto="2019:06:15 14:30:00")
    plan_path = tmp_path / "plan.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "photoarchive", "--computer", str(root.parent), "--plan", str(plan_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Файлов просмотрено:" in completed.stdout
    assert "сухой прогон" in completed.stdout
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["summary"]["files_seen"] >= 1
    assert "needs_review" not in plan_path.read_text(encoding="utf-8")
