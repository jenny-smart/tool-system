import ast
import calendar
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parents[1]


def load_functions(filename, names):
    source = ROOT / "tools" / "scheduled_monthly" / filename
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")],
            level=0,
        ),
        *[
        node
        for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name in names
        ],
    ]
    namespace = {"calendar": calendar}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
            str(source),
            "exec",
        ),
        namespace,
    )
    return namespace


def test_prepaid_uses_four_complete_service_months():
    functions = load_functions(
        "prepaid_report.py",
        {"add_months", "parse_yyyymm", "period_month", "resolve_prepaid_ranges"},
    )

    october = functions["resolve_prepaid_ranges"](
        SimpleNamespace(period="202610-2", start=None, end=None)
    )
    november = functions["resolve_prepaid_ranges"](
        SimpleNamespace(period="202611-2", start=None, end=None)
    )

    assert (october["clean_date_s"], october["clean_date_e"]) == (
        "2026-11-01",
        "2027-02-28",
    )
    assert (november["clean_date_s"], november["clean_date_e"]) == (
        "2026-12-01",
        "2027-03-31",
    )


def test_stored_value_prepaid_uses_actual_month_end():
    functions = load_functions(
        "stored_value_prepaid.py",
        {"add_months", "parse_yyyymm", "period_month", "resolve_ranges"},
    )

    october = functions["resolve_ranges"](
        SimpleNamespace(period="202610-2", start=None, end=None)
    )
    november = functions["resolve_ranges"](
        SimpleNamespace(period="202611-2", start=None, end=None)
    )

    assert (october["clean_date_s"], october["clean_date_e"]) == (
        "2026-11-01",
        "2027-02-28",
    )
    assert (november["clean_date_s"], november["clean_date_e"]) == (
        "2026-12-01",
        "2027-03-31",
    )
