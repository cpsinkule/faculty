"""Exam letters belong to seats of an existing term-owned seating chart.

These letters identify the instructor's paper variants, not immutable document
versions. Ordering spans empty seats and aisles; only occupied seats advance the
cycle. Saved letters remain on their seats when students move or are reshuffled.
"""

import re


def version_count(value, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not 1 <= value <= 26:
        raise ValueError("Enter a whole number from 1 to 26 for exam versions A–Z.")
    return value


def unequal_counts(value):
    if type(value) is not bool:
        raise ValueError("Allow unequal version counts must be true or false.")
    return value


def natural(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", str(value)))


def occupied_rows(room, occupants):
    """Group occupied seat IDs by source row, with stable physical seat order."""
    floorplan = room.get("layout", {}).get("kind") == "floorplan"
    groups = {}
    for seat in room["seats"]:
        if seat["id"] not in occupants:
            continue
        row = str(seat.get("row_label") or "").strip()
        table = str(seat.get("group") or "") if not row and floorplan else ""
        key = ("row", row.casefold()) if row else ("table", table.casefold()) if table else ("grid", seat["row"])
        group = groups.setdefault(key, {"label": f"Row {row}" if row else table or f"Row {seat['row']}",
                                        "order": row or table or str(seat["row"]), "row": seat["row"], "seats": []})
        group["row"] = min(group["row"], seat["row"])
        group["seats"].append(seat)
    ordered = sorted(groups.values(), key=lambda group: (natural(group["order"]), group["row"]))
    for group in ordered:
        group["seats"].sort(key=(lambda seat: (natural(seat["label"]), seat.get("seat_index") or 0)) if floorplan else
                           (lambda seat: (seat["row"], seat["column"], seat.get("seat_index") or 0, natural(seat["label"]))))
    return ordered


def _nearby_pairs(room, rows):
    """Nearest occupied seats across neighboring source rows/tables.

    Each seat contributes its nearest occupied seat in the other row; their
    union is undirected. PDF box centers retain actual geometry; workbook cell
    centers are the available approximation. Ties follow the stable row order.
    Inverse squared distance gives a nearby matching pair greater importance
    than a distant pair across a gap. Normalization keeps weights at most one.
    """
    boxes = room.get("layout", {}).get("seats", {}) if room.get("layout", {}).get("kind") == "floorplan" else {}
    centers = {}
    for row in rows:
        for seat in row["seats"]:
            box = boxes.get(seat["label"])
            centers[seat["id"]] = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2) if box else (
                seat["column"] + (seat.get("column_span") or 1) / 2,
                seat["row"] + (seat.get("row_span") or 1) / 2)
    distances = {}
    for first, second in zip(rows, rows[1:]):
        left = [seat["id"] for seat in first["seats"]]
        right = [seat["id"] for seat in second["seats"]]
        left_best, right_best = {}, {}
        for a in left:
            for b in right:
                ax, ay = centers[a]
                bx, by = centers[b]
                distance = max((ax - bx) ** 2 + (ay - by) ** 2, 1e-9)
                if a not in left_best or distance < left_best[a][1]:
                    left_best[a] = (b, distance)
                if b not in right_best or distance < right_best[b][1]:
                    right_best[b] = (a, distance)
        for a, (b, distance) in left_best.items():
            distances[(a, b)] = distance
        for b, (a, distance) in right_best.items():
            distances[(a, b)] = distance
    scale = min(distances.values(), default=1)
    return [(a, b, scale / distance) for (a, b), distance in distances.items()]


def _relax_versions(room, rows, assigned, count):
    """Deterministic bounded local improvement; equal totals are not a constraint.

    Start with the balanced cycle. Whole-row letter rotations and individual
    recolors must strictly reduce weighted cross-row repeats. Neither can make
    consecutive occupied seats in a row share a letter. A row rotation matters
    particularly for two versions, where individual recolors usually cannot
    change a legal alternating row. This is a heuristic, not a global optimum.
    """
    if count == 1 or len(rows) < 2:
        return assigned
    groups = [[seat["id"] for seat in row["seats"]] for row in rows]
    group_of = {seat_id: index for index, group in enumerate(groups) for seat_id in group}
    row_edges = [[] for group in groups]
    neighbors = {seat_id: [] for seat_id in assigned}
    for a, b, weight in _nearby_pairs(room, rows):
        neighbors[a].append((b, weight))
        neighbors[b].append((a, weight))
        row_edges[group_of[a]].append((a, b, weight))
        row_edges[group_of[b]].append((b, a, weight))
    letters = [chr(65 + index) for index in range(count)]
    # At most 16 sweeps keeps the 2,000-seat import bound practical. Every
    # accepted change improves the same fixed objective, so no step oscillates.
    for _ in range(16):
        changed = False
        for group, edges in zip(groups, row_edges):
            best_shift = 0
            best = sum(weight for a, b, weight in edges if assigned[a] == assigned[b])
            for shift in range(1, count):
                score = sum(weight for a, b, weight in edges
                            if letters[(ord(assigned[a]) - 65 + shift) % count] == assigned[b])
                if score < best - 1e-12:
                    best_shift, best = shift, score
            if best_shift:
                for seat_id in group:
                    assigned[seat_id] = letters[(ord(assigned[seat_id]) - 65 + best_shift) % count]
                changed = True
            for index, seat_id in enumerate(group):
                forbidden = {assigned[group[other]] for other in (index - 1, index + 1) if 0 <= other < len(group)}
                best_letter = assigned[seat_id]
                best = sum(weight for other, weight in neighbors[seat_id] if assigned[other] == best_letter)
                for letter in letters:
                    if letter in forbidden:
                        continue
                    score = sum(weight for other, weight in neighbors[seat_id] if assigned[other] == letter)
                    if score < best - 1e-12:
                        best_letter, best = letter, score
                if best_letter != assigned[seat_id]:
                    assigned[seat_id] = best_letter
                    changed = True
        if not changed:
            break
    return assigned


def automatic_versions(room, occupants, count, offset=0, allow_unequal_versions=False):
    version_count(count)
    unequal_counts(allow_unequal_versions)
    rows = occupied_rows(room, occupants)
    seats = [seat for row in rows for seat in row["seats"]]
    assigned = {seat["id"]: chr(65 + (index + offset) % count) for index, seat in enumerate(seats)}
    return _relax_versions(room, rows, assigned, count) if allow_unequal_versions else assigned


def snapshot(room, roster, count, saved, check_ok):
    """Build a print/review snapshot from already-locked chart reads."""
    result = {"count": count, "rows": [], "counts": [], "total": 0,
              "unassigned": [], "conflicts": [], "ready": False}
    if count is None:
        return result
    version_count(count)
    counts = {chr(65 + index): 0 for index in range(count)}
    occupants = {student["seat_id"]: student for student in roster if student["status"] == "seated"}
    for group in occupied_rows(room, occupants):
        students = []
        previous = None
        for seat in group["seats"]:
            student = occupants[seat["id"]]
            letter = saved.get(seat["id"])
            letter = letter if letter in counts else None
            entry = {"seat_id": seat["id"], "enrollment_id": student["enrollment_id"], "name": student["name"],
                     "section_number": student["section_number"], "seat": seat["label"], "version": letter}
            students.append(entry)
            result["total"] += 1
            if letter is None:
                result["unassigned"].append(seat["label"])
            else:
                counts[letter] += 1
                if count > 1 and previous and previous["version"] == letter:
                    result["conflicts"].append({"row": group["label"], "seats": [previous["seat"], seat["label"]], "version": letter})
            previous = entry
        result["rows"].append({"label": group["label"], "students": students})
    result["counts"] = [{"version": letter, "count": total} for letter, total in counts.items()]
    result["ready"] = bool(result["total"] and not result["unassigned"] and check_ok and not room.get("review_required"))
    return result
