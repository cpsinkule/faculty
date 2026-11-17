"""Descriptive comparisons against instructor-verified work, never accuracy claims."""
from .consensus import tokens


def distance(left, right):
    """Exact Levenshtein distance with bit vectors for class-sized comparisons."""
    if len(right) > len(left):
        left, right = right, left
    if not right:
        return len(left)
    matches = {}
    for index, value in enumerate(right):
        matches[value] = matches.get(value, 0) | (1 << index)
    mask, high = (1 << len(right)) - 1, 1 << (len(right) - 1)
    positive, negative, result = mask, 0, len(right)
    for value in left:
        equal = matches.get(value, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        add = negative | ~(horizontal | positive)
        remove = positive & horizontal
        result += bool(add & high) - bool(remove & high)
        add, remove = (add << 1) | 1, remove << 1
        positive = (remove | ~(vertical | add)) & mask
        negative = add & vertical
    return result


def report(records):
    chars = errors = symbols = symbol_errors = operators = operator_errors = 0
    exact = point_error = proposed_count = overrides = corrections = reviews = math_errors = grading_count = 0
    tp = fp = fn = false_deductions = missed_deductions = predicted_deductions = true_deductions = 0
    operators_set = {"+", "-", "=", "<", ">", "^", "_", "/", "*", "\\frac", "\\leq", "\\geq", "\\times", "\\cdot"}
    for record in records:
        expected, proposed = record["expected"], record["proposed"]
        truth = expected["transcript"]["plain_text"]
        read = proposed["transcript"]["plain_text"]
        # Bound retained text and bit-vector work on unusually long responses.
        if max(len(truth), len(read)) > 20000:
            raise ValueError("Benchmark text exceeds 20,000 characters per response.")
        chars += len(truth)
        errors += distance(truth, read)
        a, b = tokens(expected["transcript"].get("latex") or truth), tokens(proposed["transcript"].get("latex") or read)
        symbols += len(a)
        symbol_errors += distance(a, b)
        math_errors += a != b
        aa, bb = [s for s in a if s in operators_set], [s for s in b if s in operators_set]
        operators += len(aa)
        operator_errors += distance(aa, bb)
        corrections += truth != read
        if "applications" not in proposed:
            continue
        grading_count += 1
        true_ids = {a["rubric_item_id"] for a in expected["applications"] if a["applied"]}
        proposed_ids = {a["rubric_item_id"] for a in proposed["applications"] if a["applied"]}
        tp += len(true_ids & proposed_ids)
        fp += len(proposed_ids - true_ids)
        fn += len(true_ids - proposed_ids)
        deductions = {i["id"] for i in record["rubric"]["items"] if i["points"] < 0}
        predicted_deductions += len(proposed_ids & deductions)
        true_deductions += len(true_ids & deductions)
        false_deductions += len((proposed_ids - true_ids) & deductions)
        missed_deductions += len((true_ids - proposed_ids) & deductions)
        changed = true_ids != proposed_ids or truth != read
        if expected.get("score") and proposed.get("score"):
            actual, predicted = expected["score"]["score"], proposed["score"]["score"]
            exact += abs(actual - predicted) < 1e-8
            point_error += abs(actual - predicted)
            proposed_count += 1
            changed |= abs(actual - predicted) >= 1e-8
        overrides += changed
        reviews += proposed.get("state") != "proposed"
    def ratio(a, b):
        return a / b if b else None
    n = len(records)
    return {"responses": n, "scored_pairs": proposed_count, "text_cer": ratio(errors, chars),
        "math_transcription_error_rate": ratio(math_errors, n), "symbol_disagreement_rate": ratio(symbol_errors, symbols),
        "operator_sign_error_rate": ratio(operator_errors, operators), "human_transcription_correction_rate": ratio(corrections, n),
        "exact_score_agreement": ratio(exact, proposed_count), "mean_absolute_point_error": ratio(point_error, proposed_count),
        "rubric_item_precision": ratio(tp, tp+fp), "rubric_item_recall": ratio(tp, tp+fn),
        "false_deduction_rate": ratio(false_deductions, predicted_deductions), "missed_deduction_rate": ratio(missed_deductions, true_deductions),
        "human_override_rate": ratio(overrides, grading_count), "review_rate": ratio(reviews, grading_count),
        "note": "Observed agreement on this instructor-reviewed sample; not a calibrated probability or an unbiased estimate of class-wide accuracy."}
