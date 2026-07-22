"""Render rich Transcripts to md / txt / html / pdf / json / zip / csv."""

from __future__ import annotations

import base64
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from .rich import Attachment, Message, Transcript, format_ts


def render(
    transcript: Transcript,
    fmt: str,
    *,
    include_tools: bool = True,
    include_thinking: bool = False,
) -> Tuple[bytes, str, str]:
    """
    Returns (data, media_type, filename).

    Formats: md | txt | html | pdf | json | zip | csv | xml | yaml
    """
    fmt = (fmt or "md").lower().strip(".")
    raw_title = transcript.title or transcript.session_id or "transcript"
    # Avoid ugly raw filenames like "foo.jsonl"
    if raw_title.endswith(".jsonl"):
        raw_title = raw_title[: -len(".jsonl")]
    safe_title = _slug(raw_title)[:60]

    if fmt == "txt" or fmt == "text":
        data = _to_txt(transcript, include_tools=include_tools, include_thinking=include_thinking).encode("utf-8")
        return data, "text/plain; charset=utf-8", f"{safe_title}.txt"

    if fmt == "md" or fmt == "markdown":
        data = _to_md(transcript, include_tools=include_tools, include_thinking=include_thinking).encode("utf-8")
        return data, "text/markdown; charset=utf-8", f"{safe_title}.md"

    if fmt == "html" or fmt == "htm":
        data = _to_html(transcript, include_tools=include_tools, include_thinking=include_thinking).encode("utf-8")
        return data, "text/html; charset=utf-8", f"{safe_title}.html"

    if fmt == "json":
        data = json.dumps(_to_json_obj(transcript), ensure_ascii=False, indent=2).encode("utf-8")
        return data, "application/json; charset=utf-8", f"{safe_title}.json"

    if fmt == "pdf":
        pdf = _to_pdf(transcript, include_tools=include_tools, include_thinking=include_thinking)
        return pdf, "application/pdf", f"{safe_title}.pdf"

    if fmt == "zip":
        z = _to_zip_bundle(transcript, include_tools=include_tools, include_thinking=include_thinking)
        return z, "application/zip", f"{safe_title}-bundle.zip"

    if fmt == "csv":
        data = _to_csv(transcript, include_tools=include_tools, include_thinking=include_thinking).encode("utf-8")
        return data, "text/csv; charset=utf-8", f"{safe_title}.csv"

    if fmt == "xml":
        data = _to_xml(transcript, include_tools=include_tools, include_thinking=include_thinking).encode("utf-8")
        return data, "application/xml; charset=utf-8", f"{safe_title}.xml"

    if fmt in ("yaml", "yml"):
        data = _to_yaml(transcript, include_tools=include_tools, include_thinking=include_thinking).encode("utf-8")
        return data, "application/yaml; charset=utf-8", f"{safe_title}.yaml"

    raise ValueError(
        f"Unsupported format: {fmt}. Use: md, txt, html, pdf, json, zip, csv, xml, yaml"
    )


SUPPORTED_FORMATS = ("md", "txt", "html", "pdf", "json", "zip", "csv", "xml", "yaml")


def clipboard_text(transcript: Transcript, *, prefer: str = "md", include_tools: bool = True) -> str:
    """Plain/markdown text suitable for pasting into another agent."""
    if prefer in ("md", "markdown"):
        return _to_md(transcript, include_tools=include_tools, include_thinking=False, for_agent_paste=True)
    return _to_txt(transcript, include_tools=include_tools, include_thinking=False)


# ---------- renderers ----------

def _header_lines(t: Transcript) -> List[str]:
    lines = [
        f"# {t.title}",
        "",
        f"- **Source:** {t.meta.get('provider') or t.source}",
        f"- **Session:** `{t.session_id}`",
    ]
    if t.cwd:
        lines.append(f"- **CWD:** `{t.cwd}`")
    if t.path:
        lines.append(f"- **File:** `{t.path}`")
    lines.append(f"- **Messages:** {t.message_count}")
    if t.image_count:
        lines.append(f"- **Images:** {t.image_count}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _to_txt(t: Transcript, *, include_tools: bool, include_thinking: bool) -> str:
    parts = [
        t.title,
        f"Source: {t.meta.get('provider') or t.source}",
        f"Session: {t.session_id}",
        f"CWD: {t.cwd}",
        "=" * 72,
        "",
    ]
    for m in t.messages:
        ts = format_ts(m.timestamp)
        role = m.role.upper()
        parts.append(f"[{role}]{(' ' + ts) if ts else ''}")
        parts.append("-" * 40)
        if include_thinking and m.thinking:
            parts.append("[thinking]")
            parts.append(m.thinking)
            parts.append("")
        if m.text:
            parts.append(m.text)
        for a in m.attachments:
            if a.kind == "image":
                parts.append(f"[image: {a.name} ({a.media_type})]")
            elif a.path:
                parts.append(f"[file: {a.name} path={a.path}]")
            elif a.text:
                parts.append(f"[attachment: {a.name}]\n{a.text}")
            else:
                parts.append(f"[attachment: {a.name}]")
        if include_tools:
            for tc in m.tool_calls:
                parts.append(f"[tool_call] {tc.get('name')}")
                try:
                    parts.append(json.dumps(tc.get("input"), ensure_ascii=False, indent=2)[:8000])
                except Exception:
                    parts.append(str(tc.get("input"))[:8000])
            for tr in m.tool_results:
                parts.append(f"[tool_result] {tr.get('tool_use_id') or ''}")
                parts.append(str(tr.get("content") or "")[:12000])
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _to_md(
    t: Transcript,
    *,
    include_tools: bool,
    include_thinking: bool,
    for_agent_paste: bool = False,
) -> str:
    parts = _header_lines(t)
    if for_agent_paste:
        parts.insert(0, "<!-- Exported conversation for pasting into another coding agent -->\n")

    img_index = 0
    for m in t.messages:
        ts = format_ts(m.timestamp)
        role = m.role
        heading = f"## {role}"
        if ts:
            heading += f" · {ts}"
        parts.append(heading)
        parts.append("")

        if include_thinking and m.thinking:
            parts.append("<details><summary>Thinking</summary>")
            parts.append("")
            parts.append("```")
            parts.append(m.thinking.rstrip())
            parts.append("```")
            parts.append("</details>")
            parts.append("")

        if m.text:
            parts.append(m.text.rstrip())
            parts.append("")

        for a in m.attachments:
            if a.kind == "image" and a.data_base64:
                img_index += 1
                # Embed for portability when pasting into tools that accept data URIs / raw md
                parts.append(f"![{a.name}](data:{a.media_type or 'image/png'};base64,{a.data_base64})")
                parts.append("")
            elif a.kind == "image" and a.path:
                parts.append(f"![{a.name}]({a.path})")
                parts.append("")
            elif a.text:
                parts.append(f"**Attachment `{a.name}`:**")
                parts.append("")
                parts.append("```")
                parts.append(a.text[:50000])
                parts.append("```")
                parts.append("")
            elif a.path:
                parts.append(f"- Attachment: [`{a.name}`]({a.path})")
                parts.append("")
            else:
                parts.append(f"- Attachment: `{a.name}` ({a.media_type or a.kind})")
                parts.append("")

        if include_tools:
            for tc in m.tool_calls:
                parts.append(f"### 🔧 tool: `{tc.get('name')}`")
                parts.append("")
                parts.append("```json")
                try:
                    parts.append(json.dumps(tc.get("input"), ensure_ascii=False, indent=2)[:20000])
                except Exception:
                    parts.append(str(tc.get("input"))[:20000])
                parts.append("```")
                parts.append("")
            for tr in m.tool_results:
                parts.append("### 📤 tool result")
                parts.append("")
                parts.append("```")
                parts.append(str(tr.get("content") or "")[:30000])
                parts.append("```")
                parts.append("")

        parts.append("---")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _to_html(t: Transcript, *, include_tools: bool, include_thinking: bool) -> str:
    esc = html_lib.escape
    rows = []
    for m in t.messages:
        ts = esc(format_ts(m.timestamp))
        role = esc(m.role)
        body = []
        if include_thinking and m.thinking:
            body.append(
                f'<details class="thinking"><summary>Thinking</summary>'
                f'<pre>{esc(m.thinking)}</pre></details>'
            )
        if m.text:
            body.append(f'<div class="text">{esc(m.text)}</div>')
        for a in m.attachments:
            if a.kind == "image" and a.data_base64:
                body.append(
                    f'<figure class="img"><img alt="{esc(a.name)}" '
                    f'src="data:{esc(a.media_type or "image/png")};base64,{a.data_base64}" />'
                    f'<figcaption>{esc(a.name)}</figcaption></figure>'
                )
            elif a.kind == "image" and a.path:
                body.append(f'<p>Image: <a href="{esc(a.path)}">{esc(a.name)}</a></p>')
            elif a.text:
                body.append(f'<div class="file"><strong>{esc(a.name)}</strong><pre>{esc(a.text[:50000])}</pre></div>')
            else:
                body.append(f'<p class="att">Attachment: {esc(a.name)}</p>')
        if include_tools:
            for tc in m.tool_calls:
                try:
                    raw = json.dumps(tc.get("input"), ensure_ascii=False, indent=2)[:20000]
                except Exception:
                    raw = str(tc.get("input"))[:20000]
                body.append(
                    f'<div class="tool"><div class="tool-name">tool: {esc(str(tc.get("name")))}</div>'
                    f'<pre>{esc(raw)}</pre></div>'
                )
            for tr in m.tool_results:
                body.append(
                    f'<div class="tool-result"><div class="tool-name">tool result</div>'
                    f'<pre>{esc(str(tr.get("content") or "")[:30000])}</pre></div>'
                )
        rows.append(
            f'<article class="msg role-{role}">'
            f'<header><span class="role">{role}</span>'
            f'{f"<time>{ts}</time>" if ts else ""}</header>'
            f'{"".join(body)}</article>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(t.title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
         max-width: 880px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5;
         background: #0b1220; color: #e2e8f0; }}
  h1 {{ font-size: 1.4rem; margin-bottom: .25rem; }}
  .meta {{ color: #94a3b8; font-size: .85rem; margin-bottom: 2rem; }}
  .meta code {{ color: #cbd5e1; }}
  .msg {{ border: 1px solid #1e293b; border-radius: 12px; padding: 1rem 1.1rem;
          margin: 1rem 0; background: #111827; }}
  .msg header {{ display:flex; justify-content:space-between; gap:1rem;
                 font-size:.75rem; text-transform:uppercase; letter-spacing:.04em;
                 color:#94a3b8; margin-bottom:.6rem; }}
  .role-user {{ border-color: #312e81; }}
  .role-assistant {{ border-color: #134e4a; }}
  .role-terminal {{ border-color: #334155; }}
  .text {{ white-space: pre-wrap; word-break: break-word; }}
  pre {{ white-space: pre-wrap; word-break: break-word; background:#020617;
         padding:.75rem; border-radius:8px; font-size:.8rem; overflow:auto; }}
  img {{ max-width: 100%; border-radius: 8px; border: 1px solid #1e293b; }}
  .tool, .tool-result {{ margin-top:.75rem; }}
  .tool-name {{ font-size:.75rem; color:#38bdf8; margin-bottom:.25rem; }}
  .thinking {{ margin-bottom:.75rem; color:#a78bfa; }}
</style>
</head>
<body>
  <h1>{esc(t.title)}</h1>
  <div class="meta">
    <div>Source: {esc(str(t.meta.get("provider") or t.source))}</div>
    <div>Session: <code>{esc(t.session_id)}</code></div>
    <div>CWD: <code>{esc(t.cwd or "—")}</code></div>
    <div>Messages: {t.message_count} · Images: {t.image_count}</div>
  </div>
  {"".join(rows)}
  <footer class="meta">Exported by puenteo</footer>
</body>
</html>
"""


def _to_json_obj(t: Transcript) -> Dict[str, Any]:
    return {
        "source": t.source,
        "title": t.title,
        "cwd": t.cwd,
        "session_id": t.session_id,
        "path": t.path,
        "meta": t.meta,
        "messages": [
            {
                "role": m.role,
                "text": m.text,
                "timestamp": m.timestamp,
                "thinking": m.thinking,
                "tool_calls": m.tool_calls,
                "tool_results": m.tool_results,
                "attachments": [
                    {
                        "kind": a.kind,
                        "name": a.name,
                        "media_type": a.media_type,
                        "path": a.path,
                        "text": a.text,
                        "data_base64": a.data_base64,
                        "size": a.size or len(a.data_base64 or ""),
                    }
                    for a in m.attachments
                ],
            }
            for m in t.messages
        ],
    }


def _to_zip_bundle(t: Transcript, *, include_tools: bool, include_thinking: bool) -> bytes:
    """Full fidelity: md + html + json + extracted image/files."""
    buf_path = tempfile.mktemp(suffix=".zip")
    try:
        with zipfile.ZipFile(buf_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("conversation.md", _to_md(t, include_tools=include_tools, include_thinking=include_thinking))
            zf.writestr("conversation.html", _to_html(t, include_tools=include_tools, include_thinking=include_thinking))
            zf.writestr("conversation.txt", _to_txt(t, include_tools=include_tools, include_thinking=include_thinking))
            zf.writestr(
                "conversation.json",
                json.dumps(_to_json_obj(t), ensure_ascii=False, indent=2),
            )
            zf.writestr("conversation.csv", _to_csv(t, include_tools=include_tools, include_thinking=include_thinking))
            zf.writestr("conversation.xml", _to_xml(t, include_tools=include_tools, include_thinking=include_thinking))
            zf.writestr("conversation.yaml", _to_yaml(t, include_tools=include_tools, include_thinking=include_thinking))
            img_i = 0
            file_i = 0
            for m in t.messages:
                for a in m.attachments:
                    if a.data_base64:
                        try:
                            raw = base64.b64decode(a.data_base64)
                        except Exception:
                            continue
                        if a.kind == "image":
                            img_i += 1
                            name = a.name or f"image-{img_i}.png"
                            zf.writestr(f"assets/{img_i:03d}-{_slug(name)}", raw)
                        else:
                            file_i += 1
                            name = a.name or f"file-{file_i}.bin"
                            zf.writestr(f"assets/files/{file_i:03d}-{_slug(name)}", raw)
                    elif a.path and os.path.isfile(a.path):
                        file_i += 1
                        arc = f"assets/files/{file_i:03d}-{_slug(os.path.basename(a.path))}"
                        try:
                            zf.write(a.path, arcname=arc)
                        except Exception:
                            pass
                    elif a.text:
                        file_i += 1
                        zf.writestr(f"assets/files/{file_i:03d}-{_slug(a.name or 'note')}.txt", a.text)
        with open(buf_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(buf_path)
        except OSError:
            pass


def _to_pdf(t: Transcript, *, include_tools: bool, include_thinking: bool) -> bytes:
    """
    Prefer Chrome/Chromium/Edge headless HTML→PDF (great for images + Cyrillic).
    Fallback: minimal text PDF (ASCII subset; still useful).
    """
    html = _to_html(t, include_tools=include_tools, include_thinking=include_thinking)
    with tempfile.TemporaryDirectory() as td:
        html_path = os.path.join(td, "conversation.html")
        pdf_path = os.path.join(td, "conversation.pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        chrome = _find_chrome()
        if chrome:
            try:
                subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--disable-gpu",
                        "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}",
                        f"file://{html_path}",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
                if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 100:
                    with open(pdf_path, "rb") as f:
                        return f.read()
            except Exception:
                pass

        # Fallback pure-Python simple PDF (text only)
        return _simple_text_pdf(_to_txt(t, include_tools=include_tools, include_thinking=include_thinking))


def _find_chrome() -> Optional[str]:
    """Locate a Chromium-based browser for headless PDF (macOS / Linux / Windows)."""
    import sys

    candidates: List[Optional[str]] = [
        # PATH first (Linux packages, Windows if on PATH, macOS brew casks sometimes)
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
        shutil.which("msedge"),
        shutil.which("brave-browser"),
        # macOS app bundles
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        # Linux common install paths
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]
    if sys.platform == "win32":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(local, "Google", "Chrome", "Application", "chrome.exe") if local else None,
                os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(pf, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            ]
        )
    for c in candidates:
        if not c:
            continue
        if os.path.isfile(c) and (sys.platform == "win32" or os.access(c, os.X_OK)):
            return c
    return None


def _simple_text_pdf(text: str) -> bytes:
    """
    Minimal multi-page PDF. Uses Helvetica; non-latin chars replaced.
    Good fallback when Chrome is not installed.
    """
    # Escape PDF string
    def esc(s: str) -> str:
        s = s.encode("latin-1", errors="replace").decode("latin-1")
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # wrap long lines
    wrapped = []
    for line in lines:
        while len(line) > 95:
            wrapped.append(line[:95])
            line = line[95:]
        wrapped.append(line)

    lines_per_page = 60
    pages = [wrapped[i:i + lines_per_page] for i in range(0, max(len(wrapped), 1), lines_per_page)]

    objects: List[bytes] = []
    # 1: catalog, 2: pages, then page objects

    page_objs = []
    content_objs = []

    for page_lines in pages:
        stream_lines = ["BT", "/F1 9 Tf", "50 780 Td", "12 TL"]
        first = True
        for ln in page_lines:
            if first:
                stream_lines.append(f"({esc(ln)}) Tj")
                first = False
            else:
                stream_lines.append("T*")
                stream_lines.append(f"({esc(ln)}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1", errors="replace")
        content_objs.append(stream)

    # Build PDF structure
    # obj 1 catalog, 2 pages tree, 3 font, then pairs of page+content
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]

    def add_obj(data: bytes) -> int:
        offsets.append(len(out))
        obj_num = len(offsets) - 1
        out.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        out.extend(data)
        if not data.endswith(b"\n"):
            out.extend(b"\n")
        out.extend(b"endobj\n")
        return obj_num

    # We'll assign numbers manually after knowing count
    # Simpler approach: write sequentially
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]

    def write_obj(payload: bytes) -> int:
        offsets.append(len(out))
        n = len(offsets) - 1
        out.extend(f"{n} 0 obj\n".encode("ascii"))
        out.extend(payload)
        out.extend(b"\nendobj\n")
        return n

    font_n = write_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_numbers = []
    for stream in content_objs:
        content_n = write_obj(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )
        page_n = write_obj(
            (
                f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_n} 0 R /Resources << /Font << /F1 {font_n} 0 R >> >> >>"
            ).encode("ascii")
        )
        page_numbers.append(page_n)

    kids = " ".join(f"{n} 0 R" for n in page_numbers)
    pages_n = write_obj(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_numbers)} >>".encode("ascii")
    )
    # patch parent refs is hard; rewrite pages with correct parent — for simple viewers Parent optional-ish
    # Rebuild page objects is complex; many readers accept missing Parent if Kids set.
    catalog_n = write_obj(f"<< /Type /Catalog /Pages {pages_n} 0 R >>".encode("ascii"))

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(offsets)} /Root {catalog_n} 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def _to_csv(t: Transcript, *, include_tools: bool, include_thinking: bool) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["index", "role", "timestamp", "text", "thinking", "tool_calls", "attachments"])
    for i, m in enumerate(t.messages):
        tools = ""
        if include_tools and m.tool_calls:
            try:
                tools = json.dumps(m.tool_calls, ensure_ascii=False)[:8000]
            except Exception:
                tools = str(m.tool_calls)[:8000]
        atts = ",".join(a.name or a.kind for a in m.attachments)
        thinking = m.thinking if include_thinking else ""
        w.writerow([i, m.role, m.timestamp, m.text, thinking, tools, atts])
    return buf.getvalue()


def _to_xml(t: Transcript, *, include_tools: bool, include_thinking: bool) -> str:
    esc = html_lib.escape
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<transcript>",
        f"  <source>{esc(str(t.source))}</source>",
        f"  <title>{esc(t.title or '')}</title>",
        f"  <session_id>{esc(t.session_id or '')}</session_id>",
        f"  <cwd>{esc(t.cwd or '')}</cwd>",
        f"  <path>{esc(t.path or '')}</path>",
        "  <messages>",
    ]
    for m in t.messages:
        parts.append(f'    <message role="{esc(m.role)}" timestamp="{esc(m.timestamp or "")}">')
        parts.append(f"      <text>{esc(m.text or '')}</text>")
        if include_thinking and m.thinking:
            parts.append(f"      <thinking>{esc(m.thinking)}</thinking>")
        if include_tools:
            for tc in m.tool_calls:
                try:
                    raw = json.dumps(tc.get("input"), ensure_ascii=False)[:20000]
                except Exception:
                    raw = str(tc.get("input"))[:20000]
                parts.append(
                    f'      <tool_call name="{esc(str(tc.get("name") or ""))}">'
                    f"{esc(raw)}</tool_call>"
                )
            for tr in m.tool_results:
                parts.append(
                    f"      <tool_result>{esc(str(tr.get('content') or '')[:30000])}</tool_result>"
                )
        for a in m.attachments:
            parts.append(
                f'      <attachment kind="{esc(a.kind)}" name="{esc(a.name or "")}" '
                f'media_type="{esc(a.media_type or "")}" path="{esc(a.path or "")}"/>'
            )
        parts.append("    </message>")
    parts.append("  </messages>")
    parts.append("</transcript>")
    return "\n".join(parts) + "\n"


def _to_yaml(t: Transcript, *, include_tools: bool, include_thinking: bool) -> str:
    """Minimal YAML writer (stdlib only — no PyYAML)."""

    def q(s: str) -> str:
        s = s or ""
        if any(c in s for c in ':\n"#{}[]&*!|>%@`') or s.strip() != s or s == "":
            esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            return f'"{esc}"'
        return s

    lines = [
        f"source: {q(str(t.source))}",
        f"title: {q(t.title or '')}",
        f"session_id: {q(t.session_id or '')}",
        f"cwd: {q(t.cwd or '')}",
        f"path: {q(t.path or '')}",
        "messages:",
    ]
    for m in t.messages:
        lines.append(f"  - role: {q(m.role)}")
        lines.append(f"    timestamp: {q(m.timestamp or '')}")
        lines.append(f"    text: {q(m.text or '')}")
        if include_thinking and m.thinking:
            lines.append(f"    thinking: {q(m.thinking)}")
        if include_tools and m.tool_calls:
            lines.append("    tool_calls:")
            for tc in m.tool_calls:
                lines.append(f"      - name: {q(str(tc.get('name') or ''))}")
                try:
                    raw = json.dumps(tc.get("input"), ensure_ascii=False)[:8000]
                except Exception:
                    raw = str(tc.get("input"))[:8000]
                lines.append(f"        input: {q(raw)}")
    return "\n".join(lines) + "\n"


def _slug(s: str) -> str:
    s = re.sub(r"[^\w.\-]+", "-", s, flags=re.UNICODE).strip("-")
    return s or "export"
