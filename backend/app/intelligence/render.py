"""ATS-friendly resume rendering: Markdown, plain text, HTML and DOCX.

Single column, standard section names, real text (no tables, images or hidden keywords).
The DOCX writer uses only the standard library.
"""

from __future__ import annotations

import html
import io
import zipfile
from collections.abc import Callable
from typing import Any

from app.intelligence.tailoring import SECTION_TITLES

Resolver = Callable[[Any], Any]


def _included(resolve: Resolver, ref: Any) -> Any | None:
    if ref is None:
        return None
    claim = resolve(ref)
    if claim is None or not getattr(claim, "included", True):
        return None
    return claim


def _bullet_text(claim: Any) -> str:
    label = getattr(claim, "label", None)
    text = getattr(claim, "generated_text", None) or getattr(claim, "text", "")
    return (
        f"{label}: {text}"
        if label and getattr(claim, "section", "") in {"experience", "project"}
        else text
    )


def resume_blocks(content: dict[str, Any], resolve: Resolver) -> list[tuple[str, Any]]:
    """Ordered (kind, value) blocks shared by every output format."""
    blocks: list[tuple[str, Any]] = []
    header = content.get("header") or {}
    if header.get("name"):
        blocks.append(("name", header["name"]))
    if header.get("contact"):
        blocks.append(("contact", " | ".join(header["contact"])))
    headline = _included(resolve, content.get("headline"))
    if headline:
        blocks.append(("headline", _bullet_text(headline)))
    for section in content.get("section_order") or list(SECTION_TITLES):
        if section == "summary":
            sentences = [
                _bullet_text(c)
                for c in (_included(resolve, r) for r in content.get("summary", []))
                if c
            ]
            if sentences:
                blocks.append(("heading", SECTION_TITLES["summary"]))
                blocks.append(("paragraph", " ".join(sentences)))
        elif section == "experience" and content.get("experience"):
            blocks.append(("heading", SECTION_TITLES["experience"]))
            for entry in content["experience"]:
                title = entry.get("title") or ""
                employer = entry.get("employer") or ""
                meta = " | ".join(
                    part for part in (entry.get("location"), entry.get("dates")) if part
                )
                blocks.append(("entry", {"title": title, "org": employer, "meta": meta}))
                for ref in entry.get("bullets", []):
                    claim = _included(resolve, ref)
                    if claim:
                        blocks.append(("bullet", _bullet_text(claim)))
                stack = _included(resolve, entry.get("stack"))
                if stack:
                    blocks.append(("bullet", _bullet_text(stack)))
        elif section == "skills" and content.get("skills"):
            lines = [
                _bullet_text(c) for c in (_included(resolve, r) for r in content["skills"]) if c
            ]
            if lines:
                blocks.append(("heading", SECTION_TITLES["skills"]))
                blocks.extend(("bullet", line) for line in lines)
        elif section == "projects" and content.get("projects"):
            entries = [
                entry for entry in content["projects"] if _included(resolve, entry.get("head"))
            ]
            if entries:
                blocks.append(("heading", SECTION_TITLES["projects"]))
            for entry in entries:
                head = _included(resolve, entry.get("head"))
                blocks.append(
                    (
                        "entry",
                        {"title": _bullet_text(head), "org": "", "meta": entry.get("links") or ""},
                    )
                )
                for ref in entry.get("bullets", []):
                    claim = _included(resolve, ref)
                    if claim:
                        blocks.append(("bullet", _bullet_text(claim)))
                stack = _included(resolve, entry.get("stack"))
                if stack:
                    blocks.append(("bullet", _bullet_text(stack)))
        elif section == "education":
            education = [
                c for c in (_included(resolve, r) for r in content.get("education", [])) if c
            ]
            certifications = [
                c for c in (_included(resolve, r) for r in content.get("certifications", [])) if c
            ]
            if education or certifications:
                blocks.append(("heading", SECTION_TITLES["education"]))
                blocks.extend(
                    ("bullet", _bullet_text(claim)) for claim in education + certifications
                )
    return blocks


def to_markdown(content: dict[str, Any], resolve: Resolver) -> str:
    lines: list[str] = []
    for kind, value in resume_blocks(content, resolve):
        if kind == "name":
            lines.append(f"# {value}")
        elif kind == "contact":
            lines.append(value)
        elif kind == "headline":
            lines.append(f"**{value}**")
        elif kind == "heading":
            lines.extend(["", f"## {value}"])
        elif kind == "paragraph":
            lines.append(value)
        elif kind == "entry":
            title = " \u2014 ".join(part for part in (value["title"], value["org"]) if part)
            lines.extend(["", f"**{title}**" + (f"  \n{value['meta']}" if value["meta"] else "")])
        elif kind == "bullet":
            lines.append(f"- {value}")
    return "\n".join(lines).strip() + "\n"


def to_text(content: dict[str, Any], resolve: Resolver) -> str:
    lines: list[str] = []
    for kind, value in resume_blocks(content, resolve):
        if kind in {"name", "contact", "headline", "paragraph"}:
            lines.append(value)
        elif kind == "heading":
            lines.extend(["", value.upper()])
        elif kind == "entry":
            title = " - ".join(part for part in (value["title"], value["org"]) if part)
            lines.extend(["", title] + ([value["meta"]] if value["meta"] else []))
        elif kind == "bullet":
            lines.append(f"- {value}")
    return "\n".join(lines).strip() + "\n"


def to_html(content: dict[str, Any], resolve: Resolver, title: str = "Resume") -> str:
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title>",
        "<style>body{font-family:Calibri,Arial,sans-serif;max-width:800px;margin:24px auto;"
        "color:#111;line-height:1.35;font-size:11pt}h1{font-size:20pt;margin:0}"
        "h2{font-size:12pt;text-transform:uppercase;border-bottom:1px solid #444;margin:14px 0 6px}"
        "p{margin:4px 0}ul{margin:4px 0 8px 18px;padding:0}li{margin:2px 0}"
        ".meta{color:#333}.entry{margin-top:8px;font-weight:bold}"
        "@media print{body{margin:0}}</style></head><body>",
    ]
    in_list = False
    for kind, value in resume_blocks(content, resolve):
        if kind != "bullet" and in_list:
            parts.append("</ul>")
            in_list = False
        if kind == "name":
            parts.append(f"<h1>{html.escape(value)}</h1>")
        elif kind == "contact":
            parts.append(f'<p class="meta">{html.escape(value)}</p>')
        elif kind == "headline":
            parts.append(f"<p><strong>{html.escape(value)}</strong></p>")
        elif kind == "heading":
            parts.append(f"<h2>{html.escape(value)}</h2>")
        elif kind == "paragraph":
            parts.append(f"<p>{html.escape(value)}</p>")
        elif kind == "entry":
            title = " \u2014 ".join(part for part in (value["title"], value["org"]) if part)
            parts.append(f'<p class="entry">{html.escape(title)}</p>')
            if value["meta"]:
                parts.append(f'<p class="meta">{html.escape(value["meta"])}</p>')
        elif kind == "bullet":
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{html.escape(value)}</li>")
    if in_list:
        parts.append("</ul>")
    parts.append("</body></html>")
    return "".join(parts)


_DOCX_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)


def _run(text: str, *, bold: bool = False, size: int = 21) -> str:
    properties = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr>'
    return (
        f'<w:r>{properties}<w:t xml:space="preserve">{html.escape(text, quote=False)}</w:t></w:r>'
    )


def _paragraph(runs: str, *, indent: bool = False, space_before: int = 0) -> str:
    spacing = f'<w:spacing w:before="{space_before}" w:after="40"/>'
    indentation = '<w:ind w:left="360" w:hanging="200"/>' if indent else ""
    return f"<w:p><w:pPr>{spacing}{indentation}</w:pPr>{runs}</w:p>"


def to_docx(content: dict[str, Any], resolve: Resolver) -> bytes:
    body: list[str] = []
    for kind, value in resume_blocks(content, resolve):
        if kind == "name":
            body.append(_paragraph(_run(value, bold=True, size=36)))
        elif kind == "contact":
            body.append(_paragraph(_run(value, size=19)))
        elif kind == "headline":
            body.append(_paragraph(_run(value, bold=True)))
        elif kind == "heading":
            body.append(_paragraph(_run(value.upper(), bold=True, size=23), space_before=200))
        elif kind == "paragraph":
            body.append(_paragraph(_run(value)))
        elif kind == "entry":
            title = " \u2014 ".join(part for part in (value["title"], value["org"]) if part)
            body.append(_paragraph(_run(title, bold=True), space_before=120))
            if value["meta"]:
                body.append(_paragraph(_run(value["meta"], size=19)))
        elif kind == "bullet":
            body.append(_paragraph(_run("\u2022 " + value), indent=True))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="850" w:right="850" '
        'w:bottom="850" w:left="850" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()
