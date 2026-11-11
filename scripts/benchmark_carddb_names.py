#!/usr/bin/env python3
r"""Measure CardDB named lookup performance on a local SQLite snapshot.

Run from PowerShell:
    python .\scripts\benchmark_carddb_names.py --name "Sol Ring"

The benchmark imports the *local repository source*, not the PyPI package.
Each trial starts in a fresh Python process. OS file caches are not cleared.
No database writes, downloads, or additional dependencies are required.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from time import perf_counter


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="repository root (default: current directory)")
    parser.add_argument("--database", type=Path, default=None,
                        help="database path (default: CardDB's per-user path)")
    parser.add_argument("--name", default="Sol Ring",
                        help="exact card/face name to search (default: Sol Ring)")
    parser.add_argument("--runs", type=int, default=5,
                        help="fresh-process trials per mode (default: 5)")
    parser.add_argument("--repeat", type=int, default=50,
                        help="name searches per repeated-lookup trial (default: 50)")
    parser.add_argument("--output", type=Path, default=None,
                        help="save detailed results as JSON")
    parser.add_argument("--compare", type=Path, default=None,
                        help="compare medians with an earlier JSON result")
    parser.add_argument("--worker", choices=("oneoff", "repeat"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.runs < 1 or args.repeat < 1:
        parser.error("--runs and --repeat must be positive integers")
    args.repo = args.repo.expanduser().resolve()
    if args.database is not None:
        args.database = args.database.expanduser().resolve()
    source = args.repo / "src" / "mtg_carddb" / "database.py"
    if not source.is_file():
        parser.error(f"could not find CardDB source at {source}; specify --repo")
    return args


def milliseconds(start: float, end: float) -> float:
    return (end - start) * 1000.0


def worker(args: argparse.Namespace) -> None:
    # Explicitly import from the selected checkout, not site-packages.
    sys.path.insert(0, str(args.repo / "src"))
    start = perf_counter()
    from mtg_carddb import CardDatabase, default_database_path
    imported = perf_counter()

    db_path = args.database if args.database is not None else default_database_path()
    if not db_path.is_file():
        raise SystemExit(f"CardDB database not found: {db_path}. Run 'mtg-carddb init', "
                         "or pass --database PATH.")

    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        opened = perf_counter()
        ids = db.by_name(args.name)
        found = perf_counter()
        cards = db.resolve(ids)
        resolved = perf_counter()
        if not ids:
            raise SystemExit(f"No card or face matches {args.name!r} in {db_path}")

        payload = {
            "db_path": str(db_path),
            "name": args.name,
            "ids": sorted(ids),
            "card_names": [card.name for card in cards],
            "import_ms": milliseconds(start, imported),
            "open_ms": milliseconds(imported, opened),
            "first_name_ms": milliseconds(opened, found),
            "resolve_ms": milliseconds(found, resolved),
            "open_lookup_resolve_ms": milliseconds(imported, resolved),
        }

        if args.worker == "repeat":
            started = perf_counter()
            for _ in range(args.repeat):
                assert db.by_name(args.name) == ids
            finished = perf_counter()
            payload["repeated_default_ms"] = milliseconds(started, finished) / args.repeat

            if hasattr(db, "preload_names"):
                started = perf_counter()
                db.preload_names()
                finished = perf_counter()
                payload["preload_names_ms"] = milliseconds(started, finished)
                started = perf_counter()
                for _ in range(args.repeat):
                    assert db.by_name(args.name) == ids
                finished = perf_counter()
                payload["repeated_preloaded_ms"] = milliseconds(started, finished) / args.repeat
            else:
                payload["preload_names_ms"] = None
                payload["repeated_preloaded_ms"] = None

    print(json.dumps(payload, separators=(",", ":")))


def run_worker(args: argparse.Namespace, mode: str) -> dict:
    cmd = [sys.executable, str(Path(__file__).resolve()),
           "--worker", mode, "--repo", str(args.repo),
           "--name", args.name, "--runs", "1", "--repeat", str(args.repeat)]
    if args.database is not None:
        cmd.extend(["--database", str(args.database)])
    started = perf_counter()
    completed = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    elapsed = milliseconds(started, perf_counter())
    if completed.returncode:
        raise RuntimeError(
            f"{mode} worker failed (exit {completed.returncode}):\n"
            f"{completed.stderr or completed.stdout}")
    try:
        record = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid worker output:\n{completed.stdout}") from exc
    record["fresh_process_wall_ms"] = elapsed
    return record


def aggregate(rows: list[dict], key: str) -> dict | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return None
    return {"median": statistics.median(values),
            "min": min(values), "max": max(values)}


def main(args: argparse.Namespace) -> None:
    print(f"Python: {sys.version.split()[0]}")
    print(f"Local source: {args.repo / 'src' / 'mtg_carddb'}")
    print(f"Card/face name: {args.name!r}")
    print(f"Fresh-process samples: {args.runs} per mode (+ one warmup)")
    print(f"Repeated queries: {args.repeat} per sample")
    print("All timings are milliseconds; OS filesystem caches are warm.\n")

    # Warm the OS filesystem cache once; intentionally discard this result.
    warmup = run_worker(args, "oneoff")
    oneoff = [run_worker(args, "oneoff") for _ in range(args.runs)]
    repeated = [run_worker(args, "repeat") for _ in range(args.runs)]
    for result in oneoff + repeated:
        if result["ids"] != warmup["ids"]:
            raise RuntimeError("Inconsistent lookup results across trials")

    metrics = [
        ("Fresh process: full wall time", oneoff, "fresh_process_wall_ms"),
        ("Module import", oneoff, "import_ms"),
        ("CardDatabase.open", oneoff, "open_ms"),
        ("First by_name", oneoff, "first_name_ms"),
        ("Resolve matching card(s)", oneoff, "resolve_ms"),
        ("Open + first lookup + resolve", oneoff, "open_lookup_resolve_ms"),
        ("Repeated by_name (per call)", repeated, "repeated_default_ms"),
        ("preload_names (one-time)", repeated, "preload_names_ms"),
        ("Repeated preloaded (per call)", repeated, "repeated_preloaded_ms"),
    ]
    summary = {}
    print(f"Matched {len(warmup['ids'])} Oracle ID(s): {', '.join(warmup['card_names'][:5])}")
    print(f"Database: {warmup['db_path']}\n")
    print(f"{'Measurement':<39} {'Median':>10} {'Min':>10} {'Max':>10}")
    print("-" * 73)
    for label, rows, key in metrics:
        entry = aggregate(rows, key)
        summary[key] = entry
        if entry is None:
            print(f"{label:<39} {'N/A':>10}")
        else:
            print(f"{label:<39} {entry['median']:>10.3f} "
                  f"{entry['min']:>10.3f} {entry['max']:>10.3f}")

    output = {
        "metadata": {"python": sys.version.split()[0], "repo": str(args.repo),
                     "database": warmup["db_path"], "name": args.name,
                     "oracle_ids": warmup["ids"], "runs": args.runs,
                     "repeat": args.repeat},
        "summary": summary,
        "oneoff_samples": oneoff,
        "repeated_samples": repeated,
    }
    if args.compare is not None:
        reference = json.loads(args.compare.read_text(encoding="utf-8"))
        meta = reference.get("metadata", {})
        if meta.get("name") != args.name or meta.get("database") != warmup["db_path"]:
            print("\nWARNING: Baseline uses a different name or database.")
        print("\nSpeedup vs previous result (baseline / current):")
        for label, _, key in metrics:
            before = (reference.get("summary", {}).get(key) or {}).get("median")
            after = (summary.get(key) or {}).get("median")
            if before is not None and after is not None and after > 0:
                print(f"  {label:<39} {before / after:>8.2f}x")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(f"\nResults saved to {args.output}")
    print("\nInterpretation: compare open/first lookup/resolve for one-off usage;")
    print("compare repeated_default_ms with preloaded results for heavy usage.")
    print("Fresh-process timings include process startup, but do not clear disk caches.")


if __name__ == "__main__":
    try:
        parsed = arguments()
        if parsed.worker:
            worker(parsed)
        else:
            main(parsed)
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
