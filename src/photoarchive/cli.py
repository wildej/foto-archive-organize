"""Командная строка. Без подкоманды выполняется сухой прогон plan."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from photoarchive import __version__
from photoarchive.apply import apply_plan
from photoarchive.convert import command_converter
from photoarchive.lift import format_lift_report, lift_filler
from photoarchive.plan import build_plan, load_plan, write_plan
from photoarchive.summary import format_apply_report, format_summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photoarchive",
        description=(
            "Сводит компьютерный архив и Google Photos в папки год/месяц/альбом. "
            "Без подкоманды выполняется сухой прогон: пишется план, ничего не копируется."
        ),
    )
    parser.add_argument("--version", action="version", version=f"photoarchive {__version__}")
    commands = parser.add_subparsers(dest="command")

    plan = commands.add_parser("plan", help="Сухой прогон: скан, дедуп, JSON-план и сводка")
    plan.add_argument("--computer", type=Path, help="Корень старого архива на компьютере")
    plan.add_argument("--google", type=Path, help="Корень выгрузки Google Photos / Takeout")
    plan.add_argument(
        "--plan",
        type=Path,
        default=Path("photoarchive-plan.json"),
        help="Куда записать план (по умолчанию ./photoarchive-plan.json)",
    )
    plan.add_argument(
        "--index",
        type=Path,
        help="SQLite-кэш отпечатков, чтобы повторный скан не пересчитывал хеши",
    )

    apply_cmd = commands.add_parser("apply", help="Скопировать файлы по плану в выходной каталог")
    apply_cmd.add_argument("--plan", type=Path, required=True)
    apply_cmd.add_argument("--output", type=Path, required=True, help="Корень нового архива")
    apply_cmd.add_argument(
        "--raw-converter",
        help="Команда RAW → JPEG. К ней добавятся пути входного RAW и выходного JPEG",
    )
    apply_cmd.add_argument(
        "--heic-converter",
        help="Команда HEIC → JPEG. К ней добавятся входной и выходной пути",
    )

    summary = commands.add_parser("summary", help="Показать сводку уже записанного плана")
    summary.add_argument("--plan", type=Path, required=True)

    lift = commands.add_parser(
        "lift",
        help="Убрать папки «Фото ГГГГ г» и «Разное» из уже собранного архива",
    )
    lift.add_argument("--root", type=Path, required=True, help="Корень собранного архива")
    return parser


def _normalize(argv: list[str]) -> list[str]:
    if not argv or argv[0] in {"-h", "--help", "--version"}:
        return argv
    if argv[0] not in {"plan", "apply", "summary", "lift"}:
        return ["plan", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = _normalize(list(sys.argv[1:] if argv is None else argv))
    parser = _parser()
    if not argv:
        parser.print_help()
        return 2
    args = parser.parse_args(argv)
    try:
        if args.command == "lift":
            print(format_lift_report(lift_filler(args.root)))
            return 0
        if args.command == "summary":
            print(format_summary(load_plan(args.plan)))
            return 0
        if args.command == "apply":
            plan = load_plan(args.plan)
            raw_converter = command_converter(shlex.split(args.raw_converter)) if args.raw_converter else None
            heic_converter = command_converter(shlex.split(args.heic_converter)) if args.heic_converter else None
            report = apply_plan(
                plan,
                args.output,
                raw_converter=raw_converter,
                heic_converter=heic_converter,
            )
            print(format_apply_report(report))
            return 0 if report.ok else 1
        if not args.computer and not args.google:
            print("Нужен хотя бы один вход: --computer или --google", file=sys.stderr)
            return 2
        plan = build_plan(args.computer, args.google, args.index)
        write_plan(plan, args.plan)
        print(format_summary(plan))
        print(f"План: {args.plan.resolve()}")
        return 0
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
