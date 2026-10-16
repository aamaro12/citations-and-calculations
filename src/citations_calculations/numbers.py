"""Bounded Decimal arithmetic with units, scale and an inspectable dependency graph."""

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re

from .store import EvidenceError

# English digit formats. A parenthesised amount is negative; a minus sign is preserved.
NUMBER = re.compile(
    r"(?<![\w.])(?:\(\s*[$€£]?\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*\)|[$€£]?\s*[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?!\w|\.\d)"
)


def decimal(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise EvidenceError("Numbers must be decimal strings or integers, never binary floats or booleans")
    if len(str(value)) > 100:
        raise EvidenceError("Decimal input is too long")
    try:
        result = Decimal(value)
    except InvalidOperation as e:
        raise EvidenceError("Invalid decimal number") from e
    if not result.is_finite() or abs(result) > Decimal("1e30") or (result != 0 and abs(result) < Decimal("1e-30")):
        raise EvidenceError("Number is non-finite or outside the supported range")
    return result


def tokens(text):
    found = []
    for match in NUMBER.finditer(text):
        raw = match.group().strip()
        negative = raw.startswith("(")
        clean = re.sub(r"[$€£,\s()]", "", raw).replace("−", "-")
        try:
            value = decimal(clean)
        except EvidenceError:
            continue
        found.append((raw, -abs(value) if negative else value))
    return found


def unit(spec):
    if spec in ("1", "%", "pp"):
        return ()
    if not isinstance(spec, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:/[A-Za-z][A-Za-z0-9_]*)?", spec):
        raise EvidenceError("Unit must be 1, %, pp or a named unit such as USD, shares or USD/shares")
    bits = spec.split("/")
    dims = {bits[0]: 1}
    if len(bits) == 2:
        dims[bits[1]] = dims.get(bits[1], 0) - 1
    return tuple(sorted((k, v) for k, v in dims.items() if v))


def combine(left, right, sign):
    dims = dict(left)
    for key, power in right:
        dims[key] = dims.get(key, 0) + sign * power
    return tuple(sorted((key, power) for key, power in dims.items() if power))


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    dims: tuple

    def same(self, other):
        if self.dims != other.dims:
            raise EvidenceError("Incompatible units in addition, subtraction or aggregation")


def quantity(value, unit_name, scale="1"):
    scale = decimal(scale)
    if scale <= 0:
        raise EvidenceError("Scale must be positive")
    with localcontext() as ctx:
        ctx.prec = 60
        return Quantity(decimal(value) * scale / (100 if unit_name in ("%", "pp") else 1), unit(unit_name))


def display(q, unit_name, scale="1", places=2):
    if q.dims != unit(unit_name):
        raise EvidenceError("Calculation output unit does not match its inputs and formula")
    if not isinstance(places, int) or isinstance(places, bool) or not 0 <= places <= 8:
        raise EvidenceError("Display precision must be 0 to 8 decimal places")
    scale = decimal(scale)
    if scale <= 0:
        raise EvidenceError("Output scale must be positive")
    with localcontext() as ctx:
        ctx.prec = 80
        value = q.value / scale * (100 if unit_name in ("%", "pp") else 1)
        rounded = value.quantize(Decimal(1).scaleb(-places))
    suffix = {"1": "", "%": "%", "pp": " pp"}.get(unit_name, " " + unit_name)
    scale_name = {
        Decimal("1"): "",
        Decimal("1000"): " thousand",
        Decimal("1000000"): " million",
        Decimal("1000000000"): " billion",
    }.get(scale, f" × {scale}")
    return {
        "value": format(value, "f"),
        "rounded": format(rounded, "f"),
        "display": format(rounded, ",f") + scale_name + suffix,
        "unit": unit_name,
        "scale": str(scale),
        "places": places,
    }


def evaluate(expression, variables):
    if not isinstance(expression, str) or len(expression) > 1000:
        raise EvidenceError("Formula must be a bounded arithmetic expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError, MemoryError) as e:
        if isinstance(e, SyntaxError) and "nested" not in str(e.msg):
            raise EvidenceError("Invalid formula: " + str(e.msg)) from e
        raise EvidenceError("Formula is nested too deeply") from e
    if len(list(ast.walk(tree))) > 200:
        raise EvidenceError("Formula is too complex")
    used = set()

    def visit(node):
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise EvidenceError("Formula uses an unbound input: " + node.id)
            used.add(node.id)
            return variables[node.id]
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or node.value not in (0, 1, 100):
                raise EvidenceError("Numerical literals other than 0, 1 and 100 need a declared assumption input")
            return Quantity(decimal(str(node.value)), ())
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            q = visit(node.operand)
            return Quantity(-q.value if isinstance(node.op, ast.USub) else q.value, q.dims)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                left.same(right)
                return Quantity(
                    left.value + right.value if isinstance(node.op, ast.Add) else left.value - right.value, left.dims
                )
            if isinstance(node.op, ast.Mult):
                return Quantity(left.value * right.value, combine(left.dims, right.dims, 1))
            if isinstance(node.op, ast.Div):
                if right.value == 0:
                    raise EvidenceError("Division by zero")
                return Quantity(left.value / right.value, combine(left.dims, right.dims, -1))
            if isinstance(node.op, ast.Pow):
                if right.dims or right.value != int(right.value) or abs(right.value) > 8:
                    raise EvidenceError("Exponent must be a dimensionless integer from -8 to 8")
                return Quantity(
                    left.value ** int(right.value),
                    tuple((k, v * int(right.value)) for k, v in left.dims if v * int(right.value)),
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("sum", "mean", "min", "max", "abs")
            and not node.keywords
        ):
            args = [visit(arg) for arg in node.args]
            if not args:
                raise EvidenceError("Aggregation needs inputs")
            for q in args[1:]:
                args[0].same(q)
            values = [q.value for q in args]
            if node.func.id == "abs":
                if len(args) != 1:
                    raise EvidenceError("abs needs exactly 1 input")
                result = abs(values[0])
            else:
                result = {
                    "sum": lambda: sum(values),
                    "mean": lambda: sum(values) / len(values),
                    "min": lambda: min(values),
                    "max": lambda: max(values),
                }[node.func.id]()
            return Quantity(result, args[0].dims)
        raise EvidenceError("Formula only permits arithmetic and sum, mean, min, max or abs; no Python execution")

    with localcontext() as ctx:
        ctx.prec = 60
        try:
            q = visit(tree.body)
        except RecursionError as e:
            raise EvidenceError("Formula is nested too deeply") from e
        except (InvalidOperation, ZeroDivisionError, OverflowError) as e:
            raise EvidenceError("Calculation is undefined") from e
        if used != set(variables):
            raise EvidenceError("Every declared calculation input must be used by the formula")
        if not q.value.is_finite() or abs(q.value) > Decimal("1e30"):
            raise EvidenceError("Calculation result is outside supported range")
        return q
