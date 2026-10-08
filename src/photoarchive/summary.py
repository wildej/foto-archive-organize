"""Текстовая сводка плана."""

from __future__ import annotations


def format_summary(plan: dict) -> str:
    summary = plan["summary"]
    rows = [
        ("Файлов просмотрено", summary["files_seen"]),
        ("Точных дублей схлопнуто", summary["exact_dupes_collapsed"]),
        ("Групп ресайзов", summary["resize_groups"]),
        ("Кластеров правок (оставлена правка)", summary["edit_clusters"]),
        ("Пар RAW+JPEG", summary["raw_jpeg_pairs"]),
        ("Конвертаций RAW без JPEG", summary["raw_only_conversions"]),
        ("Видео сохранено", summary["videos_kept"]),
        ("WAV сохранено", summary["audio_kept"]),
        ("Мусора пропущено", summary["junk_skipped"]),
        ("Неизвестных пропущено", summary.get("unknown_skipped", 0)),
        ("Конфликтов альбомов", summary["album_conflicts"]),
    ]
    lines = [f"{label}: {value}" for label, value in rows]
    lines.append("Политика: если в кадре есть оригинал и правка, копируется только правка.")
    lines.append(
        "Видео переносится в подпапку «Видео», WAV копируется туда же. RAW и sidecar — в «_source»."
    )
    if plan.get("mode") == "dry-run":
        lines.append("Режим: сухой прогон. Исходники не менялись, копии не создавались.")
    warnings = plan.get("warnings") or []
    if warnings:
        lines.append("Предупреждения:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def format_apply_report(report) -> str:
    lines = [
        f"Скопировано: {report.copied}",
        f"Перенесено: {report.moved}",
        f"Уже было на месте: {report.skipped}",
        f"Сконвертировано: {report.converted}",
        f"Конфликтов путей: {len(report.conflicts)}",
        f"Ошибок: {len(report.errors)}",
    ]
    for item in report.conflicts:
        lines.append(f"- конфликт, файл не перезаписан: {item}")
    for item in report.errors:
        lines.append(f"- ошибка: {item}")
    for item in report.heic_skipped:
        lines.append(f"- HEIC без JPEG: {item}")
    return "\n".join(lines)
