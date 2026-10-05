from datetime import datetime, timezone
from pathlib import Path

from photoarchive.metadata import (
    album_from_computer_path,
    album_from_google_path,
    choose_album,
    decide_taken,
    folder_parts,
    is_edited_filename,
    metadata_marks_edited,
    normalize_stem,
    parse_filename_datetime,
)


def test_filename_date_and_camera_names():
    assert parse_filename_datetime("IMG_20181103_120000.jpg") == datetime(2018, 11, 3, 12, 0, 0)
    assert parse_filename_datetime("DSC_1234.NEF") is None


def test_date_priority_exif_offset_beats_google_utc():
    google = int(datetime(2019, 12, 31, 21, 30, tzinfo=timezone.utc).timestamp())
    taken = decide_taken(
        {"dto": "2020:01:01 00:30:00", "dto_off": "+03:00", "dtd": None, "dtd_off": None, "dt": None, "dt_off": None},
        google,
        "IMG_19990101.jpg",
        1_700_000_000,
    )
    assert taken.source == "exif_datetime_original"
    assert folder_parts(taken) == ("2020", "01")


def test_google_timestamp_is_utc():
    google = int(datetime(2020, 1, 31, 12, 0, tzinfo=timezone.utc).timestamp())
    taken = decide_taken(
        {"dto": None, "dto_off": None, "dtd": None, "dtd_off": None, "dt": None, "dt_off": None},
        google,
        "pic.jpg",
        None,
    )
    assert taken.source == "google_photo_taken_time"
    assert folder_parts(taken) == ("2020", "01")


def test_google_beats_filename_and_other_exif():
    google = int(datetime(2020, 3, 2, tzinfo=timezone.utc).timestamp())
    taken = decide_taken(
        {"dto": None, "dto_off": None, "dtd": "1999:01:01 00:00:00", "dtd_off": None, "dt": None, "dt_off": None},
        google,
        "IMG_20111111_101010.jpg",
        None,
    )
    assert taken.source == "google_photo_taken_time"
    assert folder_parts(taken) == ("2020", "03")


def test_filename_beats_mtime():
    taken = decide_taken(
        {"dto": None, "dto_off": None, "dtd": None, "dtd_off": None, "dt": None, "dt_off": None},
        None,
        "IMG_20181103_120000.jpg",
        datetime(2026, 5, 1).timestamp(),
    )
    assert taken.source == "filename"
    assert folder_parts(taken) == ("2018", "11")


def test_albums_and_conflict():
    root = Path("/archive")
    assert album_from_computer_path(root / "2019" / "06" / "a.jpg", root) is None
    assert album_from_computer_path(root / "2019" / "06" / "Отпуск" / "a.jpg", root) == "Отпуск"
    google = Path("/takeout")
    album = album_from_google_path(
        google / "Takeout" / "Google Photos" / "Италия" / "a.jpg",
        google,
    )
    assert album == "Италия"
    library = album_from_google_path(
        google / "Takeout" / "Google Photos" / "Photos from 2019" / "a.jpg",
        google,
    )
    assert library is None
    choice = choose_album([("Отпуск", "computer"), ("Италия", "google")])
    assert choice.name == "Италия"
    assert choice.conflict is True
    assert ("Отпуск", "computer") in choice.alternatives


def test_edited_names_and_metadata():
    assert is_edited_filename("IMG_1-edited.jpg")
    assert is_edited_filename("IMG_1-edited (1).JPG")
    assert not is_edited_filename("IMG_1.jpg")
    assert normalize_stem("IMG_1-edited (1).JPG") == normalize_stem("IMG_1.JPG")
    assert metadata_marks_edited({"title": "shot.jpg", "edited": True})
    assert metadata_marks_edited({"title": "shot-edited.jpg"})
    assert not metadata_marks_edited({"title": "shot.jpg", "photoTakenTime": {"timestamp": "1"}})
