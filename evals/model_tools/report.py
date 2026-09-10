"""Summarize complete experiments without combining incompatible trial sets."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .run import write_summary


def load_complete(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    configuration = json.loads((directory / "configuration.json").read_text())
    records = [json.loads(line) for line in (directory / "trials.jsonl").read_text().splitlines()]
    if len(records) != configuration["trials"]:
        raise ValueError(f"Incomplete experiment: {len(records)}/{configuration['trials']}")
    keys = {(row["case"], row["model"], row["surface"], row["guidance"], row["phrasing"], row["repetition"]) for row in records}
    if len(keys) != len(records):
        raise ValueError("Duplicate trial identities")
    return configuration, records


def render_diagnostics(records: list[dict[str, Any]]) -> str:
    case_name_to_record_list: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        case_name_to_record_list[record["case"]].append(record)
    surfaces = sorted({row["surface"] for row in records})
    lines = [
        "## Task breakdown",
        "",
        "Each entry is overall passes / state-and-effect passes / trials.",
        "",
        "| Case | " + " | ".join(surfaces) + " |",
        "| --- | " + " | ".join("---" for _ in surfaces) + " |",
    ]
    for name, rows in sorted(case_name_to_record_list.items()):
        cells = []
        for surface in surfaces:
            selected = [row for row in rows if row["surface"] == surface]
            cells.append(f"{sum(row['passed'] for row in selected)} / {sum(row['state_passed'] for row in selected)} / {len(selected)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "## Matched comparisons",
            "",
            "Counts are left wins / ties / right wins, matched by model, task, wording, guidance, and repetition.",
            "",
            "| Left | Right | Metric | Wins / ties / losses |",
            "| --- | --- | --- | --- |",
        ]
    )
    for left, right in (("refined", "original"), ("merged", "refined")):
        key_to_left_record = {
            (row["model"], row["case"], row["phrasing"], row["guidance"], row["repetition"]): row for row in records if row["surface"] == left
        }
        pairs = [
            (key_to_left_record[key], row)
            for row in records
            if row["surface"] == right
            and (key := (row["model"], row["case"], row["phrasing"], row["guidance"], row["repetition"])) in key_to_left_record
        ]
        for metric in ("passed", "state_passed"):
            wins = sum(first[metric] > second[metric] for first, second in pairs)
            ties = sum(first[metric] == second[metric] for first, second in pairs)
            lines.append(f"| {left} | {right} | {metric} | {wins} / {ties} / {len(pairs) - wins - ties} |")
    lines.extend(["", "## Failure diagnostics", ""])
    for surface in surfaces:
        selected = [row for row in records if row["surface"] == surface]
        error_type_to_count = Counter(
            operation.get("response", {}).get("error_type", "unknown")
            for row in selected
            for operation in row["operations"]
            if operation.get("status") == "error"
        )
        outcome_only = sum(not row["passed"] and row["state_passed"] for row in selected)
        collision_rows = [row for row in selected if row["case"] in ("collision", "helper_collision")]
        collision_inspections = sum(any(operation["kind"] == "inspect" for operation in row["operations"]) for row in collision_rows)
        lines.append(
            f"- {surface}: {outcome_only} wrong-control-only failures; inspection used in {collision_inspections}/{len(collision_rows)} collision trials; operation errors {dict(error_type_to_count)}."
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    configuration, records = load_complete(arguments.directory)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    write_summary(arguments.output, records, configuration)
    with arguments.output.open("a") as stream:
        stream.write("\n" + render_diagnostics(records))


if __name__ == "__main__":
    main()
