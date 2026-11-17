"""Authoritative decimal scoring, separate from model rubric applications."""
from decimal import Decimal

from .models import items, number, text
from .rubrics import part_cap


def score(rubric, applications, max_points, adjustments=()):
    selected = []
    available = {item["id"]: item for item in rubric["items"]}
    seen = set()
    for application in items(applications, "Rubric applications"):
        if not isinstance(application, dict) or not isinstance(application.get("rubric_item_id"), str) or application["rubric_item_id"] not in available:
            raise ValueError("Application names an unsupported rubric item.")
        iid = application["rubric_item_id"]
        if iid in seen or type(application.get("applied")) is not bool:
            raise ValueError("Each rubric item needs one boolean decision.")
        seen.add(iid)
        if application["applied"]:
            selected.append((available[iid], application))
    ids = {item["id"] for item, _ in selected}
    groups = {g["id"]: g for g in rubric["groups"]}
    origins, group_counts, sums, reasons = {}, {}, {}, []
    for item, application in selected:
        if not set(item["requires"]) <= ids:
            raise ValueError(f"{item['id']} requires other rubric items.")
        if set(item["excludes"]) & ids:
            raise ValueError(f"{item['id']} conflicts with another selected item.")
        if not item["stackable"] and len(ids) > 1:
            raise ValueError(f"{item['id']} cannot stack with another item.")
        group = item["group"]
        group_counts[group] = group_counts.get(group, 0) + 1
        if group and groups[group]["selection"] == "single" and group_counts[group] > 1:
            raise ValueError("Mutually exclusive rubric items cannot both apply.")
        origin = application.get("origin_error_id")
        if item["points"] < 0:
            for root in application.get("origin_error_roots") or ([origin] if origin else []):
                origins.setdefault(root, []).append(item)
        sums[group] = sums.get(group, Decimal(0)) + Decimal(str(item["points"]))
    for origin_items in origins.values():
        if len(origin_items) > 1 and any(i["follow_through"] or not i["repeat_penalty_for_same_originating_error"] for i in origin_items):
            raise ValueError("Follow-through prevents repeated penalties for the same originating error.")
    returned = Decimal(0)
    for group, subtotal in sums.items():
        if group:
            sums[group] = min(Decimal(str(groups[group]["ceiling"])), max(Decimal(str(groups[group]["floor"])), subtotal))
            if part_cap(group) and sums[group] != subtotal:
                # GRADING "PART CAPS": the part's deductions stop at its worth. What the cap gave back is kept apart, since Gradescope
                # cannot cap a part and receives it as the grade's point adjustment (GRADESCOPE).
                returned += sums[group] - subtotal
                reasons.append(f"{groups[group]['description']}'s deductions stopped at its {-Decimal(str(groups[group]['floor'])):g} points.")
    total = Decimal(str(max_points)) if rubric["mode"] == "negative" else Decimal(0)
    total += sum(sums.values(), Decimal(0))
    clean_adjustments = []
    for adjustment in items(list(adjustments), "Adjustments", 30):
        if not isinstance(adjustment, dict):
            raise ValueError("Invalid point adjustment.")
        amount = number(adjustment.get("points"), "Adjustment", -max_points, max_points)
        reason = text(adjustment.get("reason"), "Adjustment reason", 4000, False)
        total += Decimal(str(amount))
        clean_adjustments.append({"points": amount, "reason": reason})
    unclamped = total
    total = min(Decimal(str(rubric["ceiling"])), max(Decimal(str(rubric["floor"])), total))
    if total != unclamped:
        reasons.append("The approved point floor/ceiling was applied.")
    return {"score": float(total), "max_score": max_points, "unclamped_score": float(unclamped),
        "selected": sorted(ids), "adjustments": clean_adjustments, "notes": reasons, **({"part_caps": float(returned)} if returned else {})}
