"""Preview or atomically import a received directory of department room charts.

Room geometry, conditions and source notes are workspace catalog data. No raw
uploads are retained or written into teaching drive folders by this command.

`--corrections` instead shows what the reviewed corrections in room_corrections.py
would change in the rooms already saved, and with `--apply` saves them.
"""
import argparse
import json
import re
from pathlib import Path

from course_storage import CourseStorage
from rooms import RoomService


def intake(directory):
    items = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".xlsx", ".pdf"):
            continue
        match = re.match(r"^([A-Za-z]{2,6}\s+\d+)\b", path.stem)
        if not match:
            raise ValueError(f"Cannot identify a room from {path.name}.")
        name = " ".join(match[1].upper().split())
        item = {"name": name, "content": path.read_bytes(), "filename": path.name}
        issues = directory / f"{name} Seat Issues.docx"
        if issues.exists():
            item.update(issues_content=issues.read_bytes(), issues_filename=issues.name)
        items.append(item)
    if not items:
        raise ValueError("No room spreadsheets or PDF layouts were found.")
    matched = {item.get("issues_filename") for item in items}
    unmatched = [path.name for path in directory.glob("*.docx") if path.name not in matched]
    if unmatched:
        raise ValueError("Seat-issue documents have no matching room chart: " + ", ".join(unmatched))
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("--corrections", action="store_true", help="Show (or with --apply save) the reviewed room corrections")
    parser.add_argument("--apply", action="store_true", help="Save the validated rooms in one catalog transaction")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name(".faculty-storage.json"))
    parser.add_argument("--database", type=Path, help="Use an explicit test catalog instead of the configured deployment")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.corrections == bool(args.directory):
        parser.error("Give a directory of room charts to import, or --corrections.")

    def catalog():
        if args.database:
            return args.database
        storage = CourseStorage(json.loads(args.config.read_text()))
        storage.check_hdd()
        return storage.database

    if args.corrections:
        result = RoomService(catalog()).apply_corrections(apply=args.apply)
    elif args.apply:
        result = RoomService(catalog()).import_many(intake(args.directory))
    else:
        result = {"preview": []}
        seen = set()
        for item in intake(args.directory):
            if item["name"] in seen:
                raise ValueError(f"More than one chart names {item['name']}.")
            seen.add(item["name"])
            chart = RoomService.parse_chart(item["content"], item["filename"], item["name"],
                                           issues_content=item.get("issues_content"), issues_filename=item.get("issues_filename"),
                                           allow_ambiguous=True)
            result["preview"].append({"name": item["name"], "seat_count": len(chart["seats"]),
                                       "broken": sum(bool(seat["broken"]) for seat in chart["seats"]),
                                       "review_required": chart.get("review_required", []), "warnings": chart["warnings"]})
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        args.report.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
