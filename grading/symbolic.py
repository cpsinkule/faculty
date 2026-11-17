"""Deterministic symbolic checks of what a student WROTE. Evidence for review, never a grade.

Nothing here solves the problem for the student, repairs a line, or awards or removes a rubric line: the
rubric pays for follow-through from an earlier error, for notation and for setting up a word problem, so a
valid or invalid step is a signal for the instructor and nothing more.

Handwriting reaches this module only as retained transcript text. A line is parsed by a small closed grammar
(numbers, single-letter variables, + - * / ^, parentheses, implicit multiplication, a few named functions) and
NEVER by `eval` or SymPy's own string parser, because the text is untrusted. Whatever does not parse with
certainty is `indeterminate`: unknown notation is never guessed into a formula.

Every check is a retained record: {type, input, result: pass | fail | indeterminate, explanation}.
"""
import math
import re
from fractions import Fraction

# v2 (review, 2026-09-22): two numbers side by side, square brackets, scientific notation, a whole number beside a fraction and a
# trailing ellipsis are no longer read as a formula; results too large to check exactly are indeterminate.
CHECKER = "symbolic-v2"
LIMIT, DEPTH = 160, 24
BITS = 4096  # The largest number a power may produce: (((9^12)^12)^12)^12 has 19,786 digits, which cannot even be printed.
TERMS = 200  # The most terms an expansion may produce: (p+q+r+s+t+u+v)^12 has 18,564 and took 22 seconds; 200 take about 0.2.
FUNCTIONS = ("sqrt", "ln", "log", "exp", "abs", "sin", "cos", "tan")
WORDS = frozenset("so the and or if is to of we by let for get ans not but are was has it in on at as be do no yes off per sum max min".split())
LEAD = re.compile(r"^\s*(?:so|thus|then|hence|therefore|∴|⇒|=>|→|->)[\s,:]+", re.I)  # A connective before a formula is markup, not mathematics.
TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?|\.\d+)|([A-Za-z]+)|(\*\*|[-+*/^()=]))")


class Unparsed(ValueError):
    """The text is not certainly a formula of the closed grammar."""


def strip_thousands(value):
    """`40,000` as 40000: a comma between groups of three digits is a thousands separator, not a list."""
    return re.sub(r"(?<![\d.])(\d{1,3}(?:,\d{3})+)(?!\d)", lambda m: m[1].replace(",", ""), value)


def unwrap_text(value):
    """`\\text{...}` and its font commands as the words inside them: the reader's layout, not content."""
    return re.sub(r"\\(?:text|mathrm|operatorname|mathbf)\s*\{([^{}]*)\}", r"\1", value)


def clean(value):
    """One spelling of common handwriting/TeX notation. It removes markup, never content."""
    value = str(value or "")
    value = strip_thousands(value)
    value = unwrap_text(value)
    for _ in range(4):
        value = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", lambda m: f"(({m[1]})/({m[2]}))", value)
    value = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", value)
    value = re.sub(r"\\(?:times|cdot)|[×·]", "*", value)
    value = re.sub(r"\\(ln|log|exp|sin|cos|tan)\b", r"\1", value)
    value = re.sub(r"\\(?:left|right|,|;|!|:|quad|qquad| )|\\\\|\$|\\\(|\\\)|\\\[|\\\]", "", value)
    value = LEAD.sub("", value)
    # A square bracket stays: `[3 4]` is a matrix, not 3 times 4, and this grammar has no matrices.
    value = value.replace("−", "-").replace("–", "-").replace("÷", "/").replace("{", "(").replace("}", ")").strip()
    body, tail = re.fullmatch(r"(.*?)([.;,…\s]*)", value, re.S).groups()
    return value if ".." in tail or "…" in tail else body  # A full stop ends a sentence; `0.333...` is not 0.333.


def parse(value, equation=False):
    """A SymPy expression, or (lhs, rhs) for an equation. Raises Unparsed for anything outside the grammar."""
    import sympy
    if re.search(r"\d\s*\\[dt]?frac\s*\{\s*\d+\s*\}", str(value or "")):
        raise Unparsed("a whole number beside a fraction: a mixed number or a product")  # 23\frac{1}{8}
    value = clean(value)
    if not value or len(value) > LIMIT:
        raise Unparsed("empty or too long")
    if re.search(r"\d[eE][+-]?\d", value):
        raise Unparsed("scientific notation")  # 1e5 is not 1 times e times 5
    tokens, position = [], 0
    while position < len(value):
        match = TOKEN.match(value, position)
        if not match:
            if value[position:].strip():
                raise Unparsed("unknown symbol")
            break
        position = match.end()
        number, word, operator = match.groups()
        if number:
            if tokens and tokens[-1][0] == "number":
                # `3 4x` or `1 500x`: a matrix row, a thousands gap, a mixed number or a misread digit. Never a product of the two.
                raise Unparsed("two numbers side by side")
            tokens.append(("number", number))
        elif word:
            if word in FUNCTIONS or word == "pi":
                tokens.append(("name", word))
            elif len(word) == 1 or (len(word) <= 3 and word.casefold() not in WORDS):
                # Adjacent letters are a product of single-letter variables (xy); a word makes the line prose, not a formula.
                tokens.extend(("variable", letter) for letter in word)
            else:
                raise Unparsed("a word, not a formula")
        else:
            tokens.append(("operator", "^" if operator == "**" else operator))
    index = 0

    def peek():
        return tokens[index] if index < len(tokens) else (None, None)

    def take():
        nonlocal index
        index += 1
        # Past the end is (None, None), exactly as `peek()` reports it. Every other caller takes a token it has already
        # peeked at; the three that compare against a parenthesis are reading work that simply ended early, and they must
        # raise `Unparsed` like any other thing this grammar cannot read. Measured 2026-09-22: an unclosed parenthesis
        # ("(12 + 8z", "sqrt(4") raised IndexError out of the closed grammar instead, which is not caught as an unreadable
        # line, so it escaped `symbolic.check_lines` and failed the whole scoring stage for that response.
        return tokens[index - 1] if index <= len(tokens) else (None, None)

    def expression(depth):
        if depth > DEPTH:
            raise Unparsed("too deeply nested")
        result = term(depth)
        while peek() in (("operator", "+"), ("operator", "-")):
            result = result + term(depth) if take()[1] == "+" else result - term(depth)
        return result

    def term(depth):
        result = factor(depth)
        while True:
            kind, text = peek()
            if (kind, text) in (("operator", "*"), ("operator", "/")):
                take()
                other = factor(depth)
                if text == "/" and other == 0:
                    raise Unparsed("division by zero")
                result = result * other if text == "*" else result / other
            elif kind in ("number", "variable", "name") or (kind, text) == ("operator", "("):
                result = result * factor(depth)  # 3x, 2(x+1), x(x-1)
            else:
                return result

    def factor(depth):
        kind, text = peek()
        if (kind, text) in (("operator", "-"), ("operator", "+")):
            take()
            return -factor(depth) if text == "-" else factor(depth)
        base = atom(depth)
        if peek() == ("operator", "^"):
            take()
            exponent = factor(depth)
            if not exponent.is_number or abs(exponent) > 12:
                if not exponent.free_symbols or len(str(exponent)) > 12:
                    raise Unparsed("exponent out of range")
            if exponent.is_number and _bits(base) * max(1, math.ceil(abs(exponent))) > BITS:
                raise Unparsed("a number too large to check exactly")
            return base ** exponent
        return base

    def atom(depth):
        kind, text = take() if index < len(tokens) else (None, None)
        if kind == "number":
            return sympy.Rational(str(Fraction(text)))  # 21.5 is exactly 43/2; decimals are never floats here.
        if kind == "variable":
            return sympy.E if text == "e" else sympy.Symbol(text, real=True)
        if kind == "name":
            if text == "pi":
                return sympy.pi
            if take() != ("operator", "("):
                raise Unparsed("a function needs parentheses")
            inner = expression(depth + 1)
            if take() != ("operator", ")"):
                raise Unparsed("unbalanced parentheses")
            return {"sqrt": sympy.sqrt, "ln": sympy.log, "log": sympy.log, "exp": sympy.exp, "abs": sympy.Abs, "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan}[text](inner)
        if (kind, text) == ("operator", "("):
            inner = expression(depth + 1)
            if take() != ("operator", ")"):
                raise Unparsed("unbalanced parentheses")
            return inner
        raise Unparsed("unexpected symbol")

    sides = []
    while True:
        sides.append(expression(0))
        if peek() == ("operator", "="):
            take()
            continue
        break
    if index != len(tokens):
        raise Unparsed("trailing symbols")
    if equation:
        if len(sides) != 2:
            raise Unparsed("not one equation")
        return sides[0], sides[1]
    if len(sides) != 1:
        raise Unparsed("an equation, not an expression")
    return sides[0]


def _bits(expression):
    """The size of the largest exact number written in an expression."""
    import sympy
    return max((max(abs(r.p).bit_length(), abs(r.q).bit_length()) for r in expression.atoms(sympy.Rational)), default=0)


def _terms(expression):
    """An upper bound on the terms expanding an expression (and putting it over one denominator) could produce, capped just above
    TERMS: a sum adds, a product multiplies, a power of a sum of k terms to the n gives at most C(k+n-1, n)."""
    if expression.is_Add:
        parts = [_terms(a) for a in expression.args]
        # Over one denominator every term meets every other term's denominator.
        together = any(a.is_Pow and a.exp.is_negative or a.is_Mul and any(f.is_Pow and f.exp.is_negative for f in a.args) for a in expression.args)
        return min(TERMS + 1, len(parts) * math.prod(parts) if together else sum(parts))
    if expression.is_Mul:
        return min(TERMS + 1, math.prod(_terms(a) for a in expression.args))
    if expression.is_Pow and expression.exp.is_Integer:
        k, n = _terms(expression.base), abs(int(expression.exp))
        return min(TERMS + 1, math.comb(k + n - 1, n)) if k > 1 else 1
    return max((_terms(a) for a in expression.args), default=1)


def _zero(expression):
    """Is this expression identically zero? Exact arithmetic on a bounded expression; None when that cannot be told."""
    import sympy
    if _terms(expression) > TERMS:
        return None
    try:
        reduced = sympy.cancel(sympy.together(sympy.expand(expression)))
        if reduced == 0:
            return True
        if reduced.is_number:
            return bool(sympy.simplify(reduced) == 0)
        if reduced.is_polynomial() or reduced.is_rational_function():
            return False
        reduced = sympy.simplify(reduced) if sympy.count_ops(reduced) <= 40 else reduced
        return True if reduced == 0 else None
    except (TypeError, ValueError, ArithmeticError, RecursionError, NotImplementedError):
        return None


def record(kind, given, result, explanation):
    return {"type": kind, "input": given, "result": result, "explanation": explanation, "checker": CHECKER}


def equivalent(left, right):
    """Are two written expressions the same value in another form? pass | fail | indeterminate. Used by the answer-key matcher."""
    try:
        a, b = parse(left), parse(right)
    except UNREADABLE as reason:
        return record("equivalent_forms", {"left": str(left), "right": str(right)}, "indeterminate", f"Not certainly a formula: {_why(reason)}.")
    try:
        same = _zero(a - b)
    except UNREADABLE:
        same = None
    return record("equivalent_forms", {"left": str(left), "right": str(right)}, "indeterminate" if same is None else "pass" if same else "fail",
                  "The two forms are the same expression." if same else "The two forms differ." if same is False else "Equality could not be decided exactly.")


def _solutions(lhs, rhs):
    """The real solutions of one written equation in one variable, only where that is exactly decidable (polynomials of low degree)."""
    import sympy
    if _terms(lhs - rhs) > TERMS:
        return None, None
    difference = sympy.expand(lhs - rhs)
    symbols = sorted(difference.free_symbols, key=str)
    if len(symbols) != 1 or not difference.is_polynomial(symbols[0]) or sympy.degree(difference, symbols[0]) > 4:
        return None, None
    found = sympy.solveset(difference, symbols[0], domain=sympy.S.Reals)
    return (symbols[0], found) if isinstance(found, sympy.FiniteSet) or found is sympy.S.EmptySet else (None, None)


def check_lines(lines):
    """Consistency of the written work itself. `lines`: [{id, text}] in order, exactly as transcribed.

    - arithmetic: a line with no variable whose two sides are different numbers (18/3 = 8);
    - equation_chain: two consecutive one-variable equations with different solutions (3x = 18, then x = 8);
    - final_value: the last `x = number` does not satisfy the first equation it follows (3(8) - 8 is not 10).
    A line that does not parse with certainty is skipped and said to be indeterminate; nothing is repaired. Nothing it reads may
    throw: a check that cannot be completed exactly (a number too large to print, review 2026-09-22) is indeterminate, because an
    exception here fails the whole scoring stage of the response.
    """
    import sympy
    checks, equations = [], []
    for line in lines[:60]:
        text_value = str(line.get("text") or "")
        if "=" not in text_value or len(text_value) > LIMIT:
            continue
        try:
            lhs, rhs = parse(text_value, equation=True)
        except UNREADABLE as reason:
            checks.append(record("parse", {"line_id": line["id"], "text": text_value}, "indeterminate", f"Not certainly one equation: {_why(reason)}."))
            continue
        if not (lhs - rhs).free_symbols:
            given = {"line_id": line["id"], "text": text_value}
            checks.append(_exactly(lambda: _arithmetic(given, lhs, rhs), "arithmetic", given))
            continue
        equations.append((line, lhs, rhs))
    for (first, a_lhs, a_rhs), (second, b_lhs, b_rhs) in zip(equations, equations[1:]):
        given = {"from": {"line_id": first["id"], "text": first["text"]}, "to": {"line_id": second["id"], "text": second["text"]}}
        checks.append(_exactly(lambda: _chain(given, first, second, (a_lhs, a_rhs), (b_lhs, b_rhs)), "equation_chain", given))
    if len(equations) >= 2:
        (first, lhs, rhs), (last, l_lhs, l_rhs) = equations[0], equations[-1]
        symbol = l_lhs if isinstance(l_lhs, sympy.Symbol) else None
        if symbol is not None and not l_rhs.free_symbols and (lhs - rhs).free_symbols == {symbol}:
            given = {"equation": {"line_id": first["id"], "text": first["text"]}, "value": {"line_id": last["id"], "text": last["text"]}}
            checks.append(_exactly(lambda: _final_value(given, first, symbol, (lhs, rhs), l_rhs), "final_value", given))
    return checks


UNREADABLE = (Unparsed, ValueError, OverflowError, ArithmeticError, RecursionError, TypeError)


def _why(reason):
    return reason if isinstance(reason, Unparsed) else "it could not be read exactly"


def _exactly(check, kind, given):
    """A check's record, or an indeterminate one when it cannot be completed exactly: never an exception."""
    try:
        return check()
    except UNREADABLE:
        return record(kind, given, "indeterminate", "Could not be evaluated exactly.")


def _arithmetic(given, lhs, rhs):
    import sympy
    same = _zero(lhs - rhs)
    return record("arithmetic", given, "indeterminate" if same is None else "pass" if same else "fail",
        "Both sides are the same number." if same else f"The two sides are different numbers: {sympy.nsimplify(lhs)} is not {sympy.nsimplify(rhs)}." if same is False else "Could not be evaluated exactly.")


def _chain(given, first, second, a, b):
    (symbol_a, set_a), (symbol_b, set_b) = _solutions(*a), _solutions(*b)
    if symbol_a is None or symbol_b is None or symbol_a != symbol_b:
        return record("equation_chain", given, "indeterminate", "Not two equations in the same single variable that can be compared exactly.")
    if set_a == set_b:
        return record("equation_chain", given, "pass", "Both lines have the same solutions.")
    if set_a & set_b and (set_a <= set_b or set_b <= set_a):
        # Keeping one root (a domain) or gaining one (squaring both sides) is ordinary algebra, not a contradiction.
        return record("equation_chain", given, "pass", "The lines share their solutions; one keeps or adds a root.")
    show = lambda found: ", ".join(f"{symbol_a} = {value}" for value in sorted(found, key=lambda v: float(v))) or "no real solution"
    return record("equation_chain", given, "fail", f"“{first['text']}” gives {show(set_a)}, but “{second['text']}” gives {show(set_b)}.")


def _final_value(given, first, symbol, equation, value):
    import sympy
    left, right = equation[0].subs(symbol, value), equation[1].subs(symbol, value)
    same = _zero(left - right)
    return record("final_value", given, "indeterminate" if same is None else "pass" if same else "fail",
        f"{symbol} = {value} satisfies “{first['text']}”." if same else f"With {symbol} = {value} the first equation reads {sympy.nsimplify(left)} = {sympy.nsimplify(right)}, which is false." if same is False else "Could not be evaluated exactly.")


def summary(checks):
    failed = [c for c in checks if c["result"] == "fail"]
    return {"checker": CHECKER, "pass": sum(c["result"] == "pass" for c in checks), "fail": len(failed), "indeterminate": sum(c["result"] == "indeterminate" for c in checks),
            "result": "fail" if failed else "pass" if any(c["result"] == "pass" for c in checks) else "indeterminate",
            "contradictions": [c["explanation"] for c in failed][:6]}
