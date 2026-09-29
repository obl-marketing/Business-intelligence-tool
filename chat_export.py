"""Export a saved STARS conversation to a self-contained HTML file.

The output opens in any browser and can be emailed as an attachment - no server,
no external assets, no extra Python dependency (we render a small, safe subset of
Markdown ourselves so STARS's tables / headings / bold / lists come through).

Public API:
    chat_to_html(chat: dict) -> str      # full HTML document
    filename_for(chat: dict) -> str      # a safe .html filename
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import re

_BRAND = "Drishyam · Orient Bell marketing analytics"


# ---------------------------------------------------------------------------
# Tiny, safe Markdown -> HTML (input is HTML-escaped FIRST, then we add tags).
# Handles: headings, bold, italic, inline code, links, bullet/numbered lists,
# pipe tables, horizontal rules, paragraphs. Anything else falls back to text.
# ---------------------------------------------------------------------------

def _inline(text: str) -> str:
    """Inline formatting on already HTML-escaped text."""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\s)([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    # [label](url) - url was escaped, so &amp; etc. are fine in href
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                  r'<a href="\2">\1</a>', text)
    return text


def _is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", line))


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _md_to_html(md: str) -> str:
    lines = html.escape(md or "").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()

        if not line.strip():
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # Heading
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # Table: current line has a pipe AND next line is a separator
        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            header = _cells(line)
            i += 2  # skip header + separator
            body = []
            while i < n and "|" in lines[i] and lines[i].strip():
                body.append(_cells(lines[i]))
                i += 1
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            rows = ""
            for r in body:
                # pad/truncate to header width
                r = (r + [""] * len(header))[:len(header)]
                rows += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>")
            continue

        # Bullet list
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i].rstrip())))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        # Numbered list
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+\.\s+", "", lines[i].rstrip())))
                i += 1
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue

        # Paragraph (gather consecutive plain lines)
        para = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"^\s*(#{1,6}\s|[-*]\s|\d+\.\s)", lines[i]
        ) and not ("|" in lines[i] and _is_table_sep(lines[i] if i + 0 < n else "")):
            # stop a paragraph before a table header too
            if "|" in lines[i] and i + 1 < n and _is_table_sep(lines[i + 1]):
                break
            para.append(lines[i].rstrip())
            i += 1
        out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Block renderers (display_blocks: text / chart / tool / export / files)
# ---------------------------------------------------------------------------

def _render_chart(spec: dict) -> str:
    try:
        x = spec.get("x", [])
        series = spec.get("series", [])
        title = html.escape(str(spec.get("title", "Chart")))
        head = "<th>Category</th>" + "".join(
            f"<th>{html.escape(str(s.get('name','')))}</th>" for s in series)
        rows = ""
        for idx, cat in enumerate(x):
            cells = "".join(
                f"<td>{html.escape(str(s.get('values', [])[idx] if idx < len(s.get('values', [])) else ''))}</td>"
                for s in series)
            rows += f"<tr><td>{html.escape(str(cat))}</td>{cells}</tr>"
        return (f'<div class="chart"><div class="chart-title">📊 {title}</div>'
                f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>")
    except Exception:
        return ""


def _render_tool(block: dict) -> str:
    name = html.escape(str(block.get("name", "tool")))
    try:
        args = json.dumps(block.get("input", {}), ensure_ascii=False)
    except Exception:
        args = str(block.get("input", ""))
    out = block.get("output", "")
    try:
        pretty = json.dumps(json.loads(out), indent=2, ensure_ascii=False)
    except Exception:
        pretty = str(out)
    return (f'<details class="evidence"><summary>Evidence · '
            f"<code>{name}</code></summary>"
            f'<div class="evidence-args"><strong>Query:</strong> '
            f"<code>{html.escape(args)}</code></div>"
            f"<pre>{html.escape(pretty)}</pre></details>")


def _render_block(block: dict) -> str:
    kind = block.get("kind")
    if kind == "text":
        return _md_to_html(block.get("content", ""))
    if kind == "chart":
        return _render_chart(block.get("spec", {}))
    if kind == "tool":
        return _render_tool(block)
    if kind == "export":
        n, m = block.get("rows", 0), block.get("cols", 0)
        note = " (first 50,000 rows)" if block.get("truncated") else ""
        return (f'<div class="export-note">📥 A downloadable data result '
                f"({n:,} rows × {m} columns{note}) was produced in the live app.</div>")
    if kind == "files":
        names = ", ".join(html.escape(str(x)) for x in block.get("names", []))
        return f'<div class="export-note">📎 Attached: {names}</div>'
    return ""


def _render_message(msg: dict) -> str:
    role = msg.get("role", "assistant")
    blocks = msg.get("display_blocks") or [{"kind": "text", "content": msg.get("content", "")}]
    body = "\n".join(b for b in (_render_block(bl) for bl in blocks) if b)
    who = "You" if role == "user" else "Drishyam"
    return (f'<div class="msg {role}"><div class="who">{who}</div>'
            f'<div class="bubble">{body}</div></div>')


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

_CSS = """
:root{--bg:#f4f6f8;--card:#fff;--ink:#1f2933;--muted:#6b7280;--line:#e5e7eb;
--user:#e8f0fe;--user-b:#c6dafc;--stars:#fff;--accent:#b5121b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:24px 16px 60px}
header{border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:22px}
header .brand{color:var(--accent);font-weight:700;letter-spacing:.3px;font-size:13px;
text-transform:uppercase}
header h1{margin:6px 0 4px;font-size:22px}
header .meta{color:var(--muted);font-size:13px}
.msg{margin:18px 0}
.msg .who{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:4px;
text-transform:uppercase;letter-spacing:.4px}
.bubble{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:12px 16px;overflow-x:auto}
.msg.user .bubble{background:var(--user);border-color:var(--user-b)}
.msg.user .who{color:var(--accent)}
.bubble p{margin:.5em 0}
.bubble h1,.bubble h2,.bubble h3,.bubble h4{margin:.7em 0 .35em;line-height:1.25}
.bubble h1{font-size:19px}.bubble h2{font-size:17px}.bubble h3{font-size:15px}
table{border-collapse:collapse;margin:10px 0;width:100%;font-size:14px}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f0f3f6;font-weight:600}
tbody tr:nth-child(even){background:#fafbfc}
code{background:#f0f3f6;padding:1px 5px;border-radius:4px;font-size:.9em;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow-x:auto;
font-size:12.5px;line-height:1.45}
pre code{background:none;color:inherit;padding:0}
hr{border:none;border-top:1px solid var(--line);margin:14px 0}
.chart-title{font-weight:600;margin:8px 0 4px}
details.evidence{margin:10px 0;border:1px dashed var(--line);border-radius:8px;
padding:6px 10px;background:#fbfcfd}
details.evidence summary{cursor:pointer;color:var(--muted);font-size:13px}
.evidence-args{font-size:12.5px;color:var(--muted);margin:6px 0}
.export-note{color:var(--muted);font-size:13px;margin:8px 0;font-style:italic}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);
color:var(--muted);font-size:12px;text-align:center}
"""


def _safe_title(chat: dict) -> str:
    return (chat.get("title") or "Drishyam chat").strip() or "Drishyam chat"


def filename_for(chat: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", _safe_title(chat)).strip("_")[:50] or "chat"
    day = (chat.get("updated_at") or chat.get("created_at") or "")[:10]
    return f"Drishyam_{base}_{day}.html" if day else f"Drishyam_{base}.html"


def chat_to_html(chat: dict) -> str:
    title = html.escape(_safe_title(chat))
    msgs = chat.get("messages", []) or []
    when = (chat.get("updated_at") or chat.get("created_at") or "")[:19].replace("T", " ")
    generated = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = "\n".join(_render_message(m) for m in msgs)
    if not body:
        body = '<div class="export-note">This conversation has no messages yet.</div>'
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{title} · Drishyam</title><style>{_CSS}</style></head><body><div class=\"wrap\">"
        f'<header><div class="brand">{html.escape(_BRAND)}</div>'
        f"<h1>{title}</h1>"
        f'<div class="meta">{len(msgs)} messages'
        f"{(' · last updated ' + html.escape(when)) if when else ''}"
        f" · exported {html.escape(generated)}</div></header>"
        f"{body}"
        '<footer>Generated by Drishyam — an AI data analyst for Orient Bell. '
        "Figures are drawn from live GA4 / Google &amp; Meta Ads / Zoho CRM at the time "
        "of the chat; verify against source systems before external use.</footer>"
        "</div></body></html>"
    )
