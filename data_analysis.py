"""Sandboxed pandas execution over user-uploaded spreadsheets / CSVs.

Why this exists: an LLM reading a markdown table will miscount and
hallucinate on real arithmetic. To guarantee that every row of an
uploaded dataset is actually processed - and that comparisons, VLOOKUPs
(merge), sorts, pivots and chart numbers are exact - the agent writes
pandas code and we EXECUTE it here, returning real computed values.

The agent-facing tool is `analyze_data(code)`; the uploaded tables are
pre-loaded as DataFrames (df1, df2, ...). See build_dataframes() for how
files become DataFrames and dataset_summary() for what the model is told.
"""
from __future__ import annotations
import io
import contextlib
import re

# Cap the number of rows we echo back in a result so a huge table can't
# blow up the context. The COMPUTATION still runs over every row; only the
# textual preview of the result is trimmed.
MAX_RESULT_ROWS = 200
MAX_RESULT_CHARS = 12000


# ---------------------------------------------------------------
# Turning uploaded files into named DataFrames
# ---------------------------------------------------------------

def build_dataframes(filename: str, data: bytes, start_index: int) -> list[dict]:
    """Parse one uploaded spreadsheet/CSV into one or more named DataFrames.

    Returns a list of dicts: {"var": "df1", "label": ..., "df": DataFrame,
    "rows": int, "cols": int}. An Excel workbook yields one entry per
    non-empty sheet. `start_index` is the number already registered so
    variable names stay unique (df1, df2, ...).
    """
    import pandas as pd

    name = (filename or "").lower()
    frames: list[tuple[str, "pd.DataFrame"]] = []  # (label, df)

    if name.endswith((".xlsx", ".xls")):
        xls = pd.ExcelFile(io.BytesIO(data))
        for sheet in xls.sheet_names:
            df = xls.parse(sheet)
            if not df.empty:
                frames.append((f"{filename} [{sheet}]", df))
    elif name.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(data))
        except Exception:
            df = pd.read_csv(io.BytesIO(data), encoding="latin-1")
        frames.append((filename, df))
    else:
        return []

    out = []
    for i, (label, df) in enumerate(frames):
        out.append({
            "var": f"df{start_index + i + 1}",
            "label": label,
            "df": df,
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
        })
    return out


def dataset_summary(datasets: list[dict]) -> str:
    """A compact, model-facing description of every loaded DataFrame:
    variable name, source, shape, and column names + dtypes with a small
    preview. This is injected into the user's turn so the model knows what
    to run analyze_data against."""
    if not datasets:
        return ""
    parts = ["The user attached spreadsheet/CSV data, already loaded as pandas "
             "DataFrames. Use the `analyze_data` tool to compute over them - never "
             "estimate from the preview below."]
    for d in datasets:
        df = d["df"]
        cols = ", ".join(f"{c} ({str(t)})" for c, t in zip(df.columns, df.dtypes))
        try:
            preview = df.head(5).to_markdown(index=False)
        except Exception:
            preview = df.head(5).to_string(index=False)
        parts.append(
            f"\n**`{d['var']}`** — from *{d['label']}* — "
            f"{d['rows']:,} rows × {d['cols']} columns.\n"
            f"Columns: {cols}\n"
            f"First 5 rows (preview only — the full {d['rows']:,} rows are in `{d['var']}`):\n"
            f"{preview}"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------
# Sandboxed execution
# ---------------------------------------------------------------

# Light-touch guard. This is an internal, password-gated, single-tenant
# analyst tool, so the threat model is "stop an obviously dangerous line",
# not "defeat a determined attacker". We block filesystem/network/import
# escapes and run with a curated builtins set (pandas' own internals keep
# their real builtins, so this only restricts the model's direct code).
_BLOCKED = re.compile(
    r"(__import__|\bimport\b|\beval\b|\bexec\b|\bopen\b|\bcompile\b|"
    r"\bos\.|\bsys\.|\bsubprocess\b|\bsocket\b|\bshutil\b|\bpathlib\b|"
    r"\bread_csv\b|\bread_excel\b|\bto_csv\b|\bto_excel\b|\bto_pickle\b|"
    r"getattr\s*\(|setattr\s*\(|globals\s*\(|locals\s*\(|vars\s*\()"
)

_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "int", "isinstance", "len", "list",
        "map", "max", "min", "print", "range", "reversed", "round", "set",
        "slice", "sorted", "str", "sum", "tuple", "zip",
    )
}


def run_analysis(code: str, datasets: list[dict]) -> str:
    """Execute the model's pandas `code` against the loaded DataFrames and
    return a text result. The model should assign its answer to `result`
    (DataFrame / Series / scalar / dict / list); print() output is also
    captured. Errors are returned as text so the agent can self-correct.
    """
    import json
    import pandas as pd
    import numpy as np

    if not datasets:
        return json.dumps({
            "error": "No spreadsheet/CSV data has been uploaded in this chat. "
                     "Ask the user to attach an Excel or CSV file first."
        })
    if not code or not code.strip():
        return json.dumps({"error": "No code provided."})
    blocked = _BLOCKED.search(code)
    if blocked:
        return json.dumps({
            "error": f"Blocked operation: '{blocked.group(0)}'. The data is already "
                     "loaded as DataFrames (df1, df2, ...); don't import, read files, "
                     "or write files - just use pandas on the provided DataFrames."
        })

    namespace: dict = {"pd": pd, "np": np, "__builtins__": _SAFE_BUILTINS}
    for d in datasets:
        namespace[d["var"]] = d["df"]

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 - intentional, sandboxed above
    except Exception as e:
        printed = stdout.getvalue().strip()
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "printed_before_error": printed[:2000] if printed else None,
            "hint": "Fix the code and call analyze_data again. Remember the DataFrames "
                    "are df1, df2, ...; assign your answer to `result`.",
        })

    printed = stdout.getvalue().strip()
    result = namespace.get("result", None)
    rendered, meta = _render_result(result, pd)

    payload: dict = {}
    if rendered is not None:
        payload["result"] = rendered
    if meta:
        payload.update(meta)
    if printed:
        payload["stdout"] = printed[:MAX_RESULT_CHARS]
    if not payload:
        payload["note"] = ("Code ran but produced no output. Assign your answer to a "
                           "variable named `result`, or use print().")
    return json.dumps(payload, default=str)


def _render_result(result, pd) -> tuple[object, dict]:
    """Serialize a `result` value for the tool response. Returns
    (rendered, meta) where meta carries shape info for tables."""
    if result is None:
        return None, {}

    if isinstance(result, pd.DataFrame):
        full_rows = int(result.shape[0])
        shown = result.head(MAX_RESULT_ROWS)
        try:
            text = shown.to_markdown(index=False)
        except Exception:
            text = shown.to_string(index=False)
        meta = {
            "result_type": "dataframe",
            "result_rows": full_rows,
            "result_cols": int(result.shape[1]),
        }
        if full_rows > MAX_RESULT_ROWS:
            meta["truncated_preview"] = (
                f"Showing first {MAX_RESULT_ROWS} of {full_rows:,} result rows. The "
                "computation used all rows; ask for an aggregation if you need the rest."
            )
        return text[:MAX_RESULT_CHARS], meta

    if isinstance(result, pd.Series):
        try:
            text = result.head(MAX_RESULT_ROWS).to_markdown()
        except Exception:
            text = result.head(MAX_RESULT_ROWS).to_string()
        return text[:MAX_RESULT_CHARS], {"result_type": "series",
                                         "result_len": int(result.shape[0])}

    # scalars / dict / list / numpy types -> stringify compactly
    try:
        import numpy as np
        if isinstance(result, (np.integer,)):
            result = int(result)
        elif isinstance(result, (np.floating,)):
            result = float(result)
        elif isinstance(result, np.ndarray):
            result = result.tolist()
    except Exception:
        pass
    return result, {"result_type": type(result).__name__}
