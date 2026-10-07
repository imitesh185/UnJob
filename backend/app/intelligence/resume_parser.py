"""Master-resume text extraction and structural parsing.

The parser only re-structures what the resume says; it never adds content. Anything it cannot
place is kept in ``other`` sections and reported in ``warnings`` so the user can review it.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date
from xml.etree import ElementTree

PARSER_VERSION = "resume-1"
BULLET_GLYPHS = "\u2022\u25e6\u25aa\u25cf\u25cb\u25a0\u25ba\u2023\u2043\u2219\uf0b7\uf0a7*\u2013-"
_GLYPH_LINE = re.compile(rf"^\s*([{re.escape(BULLET_GLYPHS)}])\s+(.*\S)?\s*$")

SECTION_HEADINGS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rf"^(?:{pattern})\s*:?$", re.I))
    for name, pattern in (
        (
            "summary",
            r"(?:professional\s+|career\s+|executive\s+)?(?:summary|profile|objective)"
            r"|about(?:\s+me)?",
        ),
        (
            "experience",
            r"(?:professional\s+|work\s+|relevant\s+)?experience"
            r"|employment(?:\s+history)?|work\s+history|career\s+history",
        ),
        (
            "skills",
            r"(?:technical\s+|core\s+|key\s+)?skills(?:\s*(?:&|and)\s*\w+)?"
            r"|core\s+competencies|technologies|technical\s+expertise|tools\s*(?:&|and)\s*technologies",
        ),
        (
            "projects",
            r"(?:relevant\s+|personal\s+|selected\s+|key\s+|academic\s+|side\s+)?projects",
        ),
        (
            "education",
            r"education(?:\s*(?:&|and)\s*(?:certifications?|training))?"
            r"|academic\s+(?:background|qualifications)",
        ),
        ("certifications", r"(?:licenses\s*(?:&|and)\s*)?certifications?(?:\s*(?:&|and)\s*\w+)?"),
        ("achievements", r"achievements|awards(?:\s*(?:&|and)\s*\w+)?|honou?rs"),
        ("publications", r"publications|patents"),
        ("languages", r"languages\s+spoken|spoken\s+languages"),
    )
)

_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE = rf"(?:{_MONTH}\.?\s+\d{{4}}|\d{{1,2}}\s*/\s*\d{{4}}|(?:19|20)\d{{2}})"
_PRESENT = r"(?:present|current|now|till\s+date|to\s+date|ongoing)"
DATE_RANGE = re.compile(rf"({_DATE})\s*(?:-|\u2013|\u2014|to|until)\s*({_DATE}|{_PRESENT})", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?){2,4}\d{2,5}")
_URL = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|[\w-]+\.(?:dev|io|me))"
    r"/[\w./%-]*",
    re.I,
)
_LOCATION = re.compile(
    r"^(?:[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*,\s*[A-Z][A-Za-z.' -]+|Remote(?:\s*\(.*\))?)$"
)
_ROLE_WORDS = re.compile(
    r"\b(engineer|developer|analyst|consultant|manager|architect|scientist|intern|lead|"
    r"associate|specialist|administrator|programmer|director|head|officer|trainee|sde|swe)\b",
    re.I,
)
_ORG_WORDS = re.compile(
    r"\b(technologies|technology|services|solutions|inc|ltd|llc|limited|labs|systems|"
    r"consulting|group|bank|corp(?:oration)?|company|software|pvt|private|global|partners)\b",
    re.I,
)
_STACK_LABEL = re.compile(
    r"^(?:tech(?:nology)?\s*stack|stack|technologies(?:\s+used)?|tools(?:\s*(?:&|and)\s*tech\w*)?|"
    r"environment|tech)\s*:\s*",
    re.I,
)
_LABEL = re.compile(r"^([A-Z][\w&/+.'() -]{2,60}?):\s+(\S.*)$")
_CERT = re.compile(
    r"\b(certified|certification|certificate)\b|\b(?:[A-Z]{2,3}-\d{3}|DP-\d{3}|AZ-\d{3})\b", re.I
)
_INSTITUTION = re.compile(
    r"\b(university|institute|college|school|academy|iit|nit|iiit|bits)\b", re.I
)
_DEGREE = re.compile(
    r"\b(bachelor|master|b\.?\s?e\b|b\.?\s?tech|m\.?\s?tech|b\.?\s?sc|m\.?\s?sc|mba|ph\.?\s?d|"
    r"diploma|b\.?\s?s\b|m\.?\s?s\b|engineering in|degree)\b",
    re.I,
)
_LINKS_LINE = re.compile(r"^(?:(?:code|live demo|demo|github|website|link|paper)\s*\|?\s*)+$", re.I)


@dataclass
class Bullet:
    text: str
    label: str | None = None
    kind: str = "accomplishment"  # or "stack"


@dataclass
class Experience:
    employer: str | None
    title: str | None
    location: str | None
    start: str | None
    end: str | None
    date_text: str | None
    bullets: list[Bullet] = field(default_factory=list)
    stack: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str
    subtitle: str | None
    links: str | None
    date_text: str | None
    bullets: list[Bullet] = field(default_factory=list)
    stack: list[str] = field(default_factory=list)


@dataclass
class SkillGroup:
    category: str
    items: list[str]
    raw: str


@dataclass
class Education:
    institution: str | None
    degree: str | None
    location: str | None
    year: str | None


@dataclass
class ParsedResume:
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    experiences: list[Experience] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    skills: list[SkillGroup] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    other: dict[str, list[str]] = field(default_factory=dict)
    section_order: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------------------


class ResumeFormatError(ValueError):
    pass


def extract_text(data: bytes, filename: str, content_type: str | None = None) -> tuple[str, str]:
    """Return ``(text, parser_name)`` for a PDF, DOCX, TXT or Markdown resume."""
    lower = filename.lower()
    if lower.endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_text(data), "pdfminer.six"
    if lower.endswith(".docx") or data[:2] == b"PK":
        return _docx_text(data), "docx-xml"
    if lower.endswith((".txt", ".md", ".text", ".markdown")) or (content_type or "").startswith(
        "text/"
    ):
        return data.decode("utf-8", errors="replace"), "plain-text"
    raise ResumeFormatError("Unsupported resume format. Upload a PDF, DOCX, TXT or Markdown file.")


def _pdf_text(data: bytes) -> str:
    from pdfminer.high_level import extract_text as pdf_extract
    from pdfminer.layout import LAParams

    try:
        text = pdf_extract(
            io.BytesIO(data), laparams=LAParams(char_margin=2.0, word_margin=0.1, line_margin=0.5)
        )
    except Exception as exc:  # noqa: BLE001 - pdfminer raises many exception types
        raise ResumeFormatError(f"The PDF could not be read: {exc.__class__.__name__}.") from exc
    if len(text.strip()) < 50:
        raise ResumeFormatError(
            "The PDF has no extractable text (it may be a scanned image). Upload a text-based "
            "PDF, DOCX or TXT file."
        )
    return text


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ResumeFormatError("The DOCX file could not be read.") from exc
    root = ElementTree.fromstring(document)
    lines: list[str] = []
    for paragraph in root.iter(f"{_W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
        is_list = paragraph.find(f"{_W}pPr/{_W}numPr") is not None
        if text.strip():
            lines.append(f"\u2022 {text.strip()}" if is_list else text.strip())
        else:
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------------------


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\ufb01", "fi").replace("\ufb02", "fl")
    return re.sub(r"[ \t]+", " ", text).strip()


def _heading(line: str) -> str | None:
    stripped = _clean(line).strip(":").strip()
    if not stripped or len(stripped) > 50 or _GLYPH_LINE.match(line):
        return None
    for name, pattern in SECTION_HEADINGS:
        if pattern.match(stripped):
            return name
    return None


@dataclass
class _Item:
    text: str
    glyph: str | None


def _items(lines: list[str]) -> list[_Item]:
    """Merge wrapped lines into logical items. A bullet glyph always starts an item; a plain
    line continues the previous item when that item is an unfinished sentence."""
    items: list[_Item] = []
    for raw in lines:
        line = _clean(raw)
        if not line:
            continue
        glyph_match = _GLYPH_LINE.match(line)
        if glyph_match and (glyph_match.group(2) or "").strip():
            glyph = glyph_match.group(1)
            # A dash or star only counts as a bullet glyph at the start of a line.
            items.append(_Item(glyph_match.group(2).strip(), glyph))
            continue
        if items:
            previous = items[-1]
            unfinished = not re.search(r"[.!?)\]]$", previous.text)
            continues = (
                previous.text.endswith(":")
                or line[0].islower()
                or line[0] in ",;&(%"
                or (len(previous.text) >= 60 and unfinished and not _looks_like_header(line))
            )
            if continues:
                previous.text = f"{previous.text} {line}".strip()
                continue
        items.append(_Item(line, None))
    return items


def _looks_like_header(line: str) -> bool:
    return bool(
        DATE_RANGE.search(line)
        or _LOCATION.match(line)
        or (len(line) <= 70 and _ROLE_WORDS.search(line) and not line.endswith("."))
    )


def _split_sections(text: str) -> tuple[list[str], dict[str, list[str]], list[str]]:
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        name = _heading(line)
        if name:
            current = name
            if name not in sections:
                sections[name] = []
                order.append(name)
            continue
        if current is None:
            header.append(line)
        else:
            sections[current].append(line)
    return header, sections, order


def parse_month(value: str | None, *, end: bool = False) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    if re.fullmatch(_PRESENT, value, re.I):
        return "present"
    match = re.fullmatch(rf"({_MONTH})\.?\s+(\d{{4}})", value, re.I)
    if match:
        month = _MONTHS[match.group(1)[:3] if match.group(1)[:4] != "sept" else "sep"]
        return f"{match.group(2)}-{month:02d}"
    match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{4})", value)
    if match:
        return f"{match.group(2)}-{int(match.group(1)):02d}"
    match = re.fullmatch(r"((?:19|20)\d{2})", value)
    if match:
        return f"{match.group(1)}-{'12' if end else '01'}"
    return None


def _split_items(raw: str) -> list[str]:
    """Split a skills list on top-level commas; ``Azure (Event Hubs, Synapse)`` yields the
    parent and each child."""
    items: list[str] = []
    depth, current = 0, ""
    for char in raw:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in ",;|" and depth == 0:
            items.append(current)
            current = ""
        else:
            current += char
    items.append(current)
    result: list[str] = []
    for item in items:
        item = item.strip().strip(".")
        if not item:
            continue
        nested = re.match(r"^(.*?)\s*\((.*)\)\s*$", item)
        if nested:
            parent = nested.group(1).strip()
            if parent:
                result.append(parent)
            result.extend(child.strip() for child in nested.group(2).split(",") if child.strip())
        else:
            result.append(item)
    return list(dict.fromkeys(result))


def _bullet(text: str) -> Bullet:
    stack = _STACK_LABEL.match(text)
    if stack:
        label = stack.group(0).strip().rstrip(":").strip()
        return Bullet(text=text[stack.end() :].strip(), label=label, kind="stack")
    labelled = _LABEL.match(text)
    if labelled and len(labelled.group(1).split()) <= 7:
        return Bullet(text=labelled.group(2).strip(), label=labelled.group(1).strip())
    return Bullet(text=text)


def _glyph_roles(items: list[_Item]) -> tuple[set[str], set[str]]:
    """(entry glyphs, bullet glyphs). With two glyph styles, the rarer one marking short
    lines is the entry marker (company or project); otherwise glyphs mark bullets."""
    lengths: dict[str, list[int]] = {}
    for item in items:
        if item.glyph:
            lengths.setdefault(item.glyph, []).append(len(item.text))
    if len(lengths) < 2:
        return set(), set(lengths)
    by_length = sorted(lengths, key=lambda glyph: sum(lengths[glyph]) / len(lengths[glyph]))
    entry = by_length[0]
    return {entry}, set(lengths) - {entry}


def _entries(lines: list[str]) -> list[tuple[list[str], list[str]]]:
    """Group a section into (header lines, bullet texts) entries."""
    items = _items(lines)
    entry_glyphs, bullet_glyphs = _glyph_roles(items)
    entries: list[tuple[list[str], list[str]]] = []
    header: list[str] = []
    bullets: list[str] = []
    for item in items:
        is_bullet = item.glyph in bullet_glyphs or (
            item.glyph is None and not entry_glyphs and len(item.text) > 90
        )
        if item.glyph in entry_glyphs or (not is_bullet and item.glyph is None):
            if bullets:
                entries.append((header, bullets))
                header, bullets = [], []
            header.append(item.text)
        else:
            bullets.append(item.text)
    if header or bullets:
        entries.append((header, bullets))
    return entries


def _parse_experience(lines: list[str], warnings: list[str]) -> list[Experience]:
    experiences: list[Experience] = []
    for header, bullet_texts in _entries(lines):
        employer = title = location = date_text = start = end = None
        rest: list[str] = []
        for line in header:
            dates = DATE_RANGE.search(line)
            if dates and date_text is None:
                date_text = dates.group(0)
                start, end = parse_month(dates.group(1)), parse_month(dates.group(2), end=True)
                line = (line[: dates.start()] + line[dates.end() :]).strip(" ,|-\u2013")
                if not line:
                    continue
            if location is None and _LOCATION.match(line):
                location = line
                continue
            rest.append(line)
        for line in rest:
            if (
                title is None
                and _ROLE_WORDS.search(line)
                and not (employer is None and _ORG_WORDS.search(line) and len(rest) > 1)
            ):
                title = line
            elif employer is None:
                employer = line
            else:
                title = title or line
        if not (employer or title):
            warnings.append(f"Experience entry without employer/title: {header[:2]}")
        experience = Experience(employer, title, location, start, end, date_text)
        for text in bullet_texts:
            bullet = _bullet(text)
            if bullet.kind == "stack":
                experience.stack = _split_items(bullet.text)
            experience.bullets.append(bullet)
        if not date_text:
            warnings.append(f"No dates found for experience entry '{employer or title}'.")
        experiences.append(experience)
    return experiences


def _parse_projects(lines: list[str]) -> list[Project]:
    projects: list[Project] = []
    for header, bullet_texts in _entries(lines):
        if not header:
            continue
        first, links, date_text = header[0], None, None
        for line in header[1:]:
            if _LINKS_LINE.match(line):
                links = line
            elif DATE_RANGE.search(line):
                date_text = DATE_RANGE.search(line).group(0)
        parts = re.split(r"\s+[\u2014\u2013:|-]\s+", first, maxsplit=1)
        project = Project(
            name=parts[0].strip(),
            subtitle=parts[1].strip() if len(parts) > 1 else None,
            links=links,
            date_text=date_text,
        )
        for text in bullet_texts:
            bullet = _bullet(text)
            if bullet.kind == "stack":
                project.stack = _split_items(bullet.text)
            project.bullets.append(bullet)
        projects.append(project)
    return projects


def _parse_skills(lines: list[str]) -> list[SkillGroup]:
    groups: list[SkillGroup] = []
    for item in _items(lines):
        match = re.match(r"^([^:]{2,60}):\s*(.+)$", item.text)
        if match:
            groups.append(
                SkillGroup(match.group(1).strip(), _split_items(match.group(2)), item.text)
            )
        else:
            groups.append(SkillGroup("Skills", _split_items(item.text), item.text))
    return groups


def _parse_education(lines: list[str]) -> tuple[list[Education], list[str]]:
    education: list[Education] = []
    certifications: list[str] = []
    for item in _items(lines):
        text = item.text
        if _CERT.search(text) and not _INSTITUTION.search(text):
            certifications.append(text)
        elif _INSTITUTION.search(text):
            education.append(Education(text, None, None, None))
        elif _DEGREE.search(text):
            if education and education[-1].degree is None:
                education[-1].degree = text
            else:
                education.append(Education(None, text, None, None))
        elif re.fullmatch(r"(?:19|20)\d{2}(?:\s*[-\u2013]\s*(?:19|20)\d{2})?", text):
            target = next((entry for entry in reversed(education) if entry.year is None), None)
            if target:
                target.year = text
        elif _LOCATION.match(text):
            target = next((entry for entry in reversed(education) if entry.location is None), None)
            if target:
                target.location = text
    return education, certifications


def _sentences(lines: list[str]) -> list[str]:
    paragraph = " ".join(_clean(line) for line in lines if line.strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph)
    return [part.strip() for part in parts if part.strip()]


def parse_resume(text: str) -> ParsedResume:
    resume = ParsedResume()
    header, sections, order = _split_sections(text)
    resume.section_order = order
    header_lines = [_clean(line) for line in header if line.strip()]
    if header_lines:
        candidate_name = header_lines[0]
        if 1 <= len(candidate_name.split()) <= 5 and not _EMAIL.search(candidate_name):
            resume.name = candidate_name
    header_text = " ".join(header_lines)
    email = _EMAIL.search(header_text)
    resume.email = email.group(0) if email else None
    without_email = _EMAIL.sub(" ", header_text)
    resume.links = list(dict.fromkeys(match.group(0) for match in _URL.finditer(without_email)))
    phone_text = _URL.sub(" ", without_email)
    phone = next(
        (
            match.group(0).strip()
            for match in _PHONE.finditer(phone_text)
            if len(re.sub(r"\D", "", match.group(0))) >= 10
        ),
        None,
    )
    resume.phone = phone
    if "summary" in sections:
        resume.summary = _sentences(sections["summary"])
    if "experience" in sections:
        resume.experiences = _parse_experience(sections["experience"], resume.warnings)
    if "projects" in sections:
        resume.projects = _parse_projects(sections["projects"])
    if "skills" in sections:
        resume.skills = _parse_skills(sections["skills"])
    for name in ("education", "certifications"):
        if name in sections:
            education, certifications = _parse_education(sections[name])
            resume.education.extend(education)
            resume.certifications.extend(certifications)
    for name, lines in sections.items():
        if name not in {
            "summary",
            "experience",
            "projects",
            "skills",
            "education",
            "certifications",
        }:
            resume.other[name] = [item.text for item in _items(lines)]
    if not resume.experiences:
        resume.warnings.append("No experience section was recognised.")
    if not resume.skills:
        resume.warnings.append("No skills section was recognised.")
    return resume


def months_between(start: str, end: str, today: date) -> int:
    start_year, start_month = (int(part) for part in start.split("-"))
    if end == "present":
        end_year, end_month = today.year, today.month
    else:
        end_year, end_month = (int(part) for part in end.split("-"))
    return max(0, (end_year - start_year) * 12 + (end_month - start_month))


def years_of_experience(experiences: list[Experience], today: date) -> float | None:
    """Total employment time from dated entries, merging overlaps."""
    spans: list[tuple[int, int]] = []
    for experience in experiences:
        if not experience.start or not experience.end:
            continue
        start_year, start_month = (int(part) for part in experience.start.split("-"))
        start_index = start_year * 12 + start_month
        if experience.end == "present":
            end_index = today.year * 12 + today.month
        else:
            end_year, end_month = (int(part) for part in experience.end.split("-"))
            end_index = end_year * 12 + end_month
        if end_index > start_index:
            spans.append((start_index, end_index))
    if not spans:
        return None
    spans.sort()
    total, current_start, current_end = 0, spans[0][0], spans[0][1]
    for start, end in spans[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    total += current_end - current_start
    return round(total / 12, 1)
