"""Structured job-description analysis.

The analyzer reads section headings ("Basic qualifications", "Nice to have", "What you'll do"),
splits requirements into items, and tags each with taxonomy terms, years of experience and
whether any one listed technology suffices ("such as Spark, Flink or Beam") or all are needed.
It is deterministic: every extracted requirement keeps the JD sentence it came from.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from app.discovery.names import company_key, is_india_location
from app.intelligence.taxonomy import (
    WEAK_CONCEPTS,
    expand_implied,
    find_domains,
    find_terms,
    group_terms,
    kind_of,
)

ANALYZER_VERSION = "jd-3"

_HEADINGS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(rf"^\W*(?:{pattern})\W*$", re.I))
    for kind, pattern in (
        (
            "preferred",
            r"(?:preferred|desired|bonus|additional|nice[- ]to[- ]have|good[- ]to[- ]have)"
            r"(?: qualifications| skills| experience| points)?(?: \(?optional\)?)?|"
            r"nice to haves?|pluses|bonus points|extra credit"
            r"|it would be (?:great|nice) if(?: you)?(?: have)?"
            r"|what would make you stand out|ways? to stand out",
        ),
        (
            "required",
            r"(?:basic|minimum|required|key|essential|mandatory)(?: qualifications| skills"
            r"| requirements| experience)?|requirements|qualifications|what you(?:'|\u2019)?ll need"
            r"|what you need|what we(?:'|\u2019)?re looking for|what we are looking for"
            r"|you (?:have|bring|might be a fit if you|will need|should have)|must[- ]haves?"
            r"|who you are|(?:all )?about you|your (?:profile|background|skills|experience)"
            r"|skills (?:and|&) (?:experience|qualifications)"
            r"|professional (?:&|and) technical skills"
            r"|what you(?:'|\u2019)?ll bring|what you bring|technical skills|skills"
            r"|experience(?: required)?|skills required"
            r"|what (?:we|you) need(?: from you)?|you are"
            r"|(?:the )?ideal candidate(?: for this (?:position|role))?"
            r"(?: (?:should|will|would|must))?(?: (?:have|be))?",
        ),
        (
            "responsibility",
            r"(?:key )?(?:job )?responsibilities|what you(?:'|\u2019)?ll do"
            r"|what you will do|(?:the |your |about the )?role|the position"
            r"|in this (?:role|position)(?:,? you will)?"
            r"|day[- ]to[- ]day|your impact|what you will be doing"
            r"|roles? (?:&|and) responsibilities"
            r"|job description|duties|the opportunity|what you(?:'|\u2019)?ll be doing|the job"
            r"|you will|your responsibilities|your mission",
        ),
        (
            "context",
            r"about (?:us|the team|the company|the business|[a-z0-9&.' -]{2,40})"
            r"|who we are|our team|the team|our mission|why join(?: us)?|company overview"
            r"|team overview|about this team",
        ),
        (
            "ignore",
            r"benefits|perks|what we offer|equal (?:employment )?opportunity|eeo"
            r"|compensation(?: and benefits)?|salary(?: range)?|pay (?:range|transparency)"
            r"|accommodations?|privacy notice|disclaimer|additional information|our commitment"
            r"|diversity(?:,? equity)?(?:,? (?:and|&) inclusion)?|life at [a-z0-9 ]+"
            r"|(?:corporate )?security responsibility",
        ),
        (
            # Neutral headings: items below them are classified by their own wording.
            "intro",
            r"overview|summary|title and summary|our purpose|purpose|description|details"
            r"|(?:job|position|role) (?:summary|overview|details)",
        ),
    )
)
# Under an unrecognised heading, a bullet's opening words still reveal what it is:
# "Have experience with ..." is a requirement, "Design and build ..." is a responsibility.
_REQUIREMENT_START = re.compile(
    r"^(?:have|has|possess(?:es)?|bring|be (?:familiar|proficient|skilled|comfortable|"
    r"experienced|able|an? (?:strong|excellent|proven))|experience (?:with|in|of|building|"
    r"developing|designing)|\d+\+?\s*(?:years?|yrs)|strong|solid|deep|proven|hands[- ]on|"
    r"proficien\w*|knowledge of|familiarity with|understanding of|expertise (?:in|with)|"
    r"working knowledge|demonstrated|excellent|bachelor\w*|master\w*|degree)\b",
    re.I,
)
_RESPONSIBILITY_START = re.compile(
    r"^(?:design|develop|build|create|implement|maintain|own|lead|drive|partner|collaborate|"
    r"work|monitor|troubleshoot|optimi[sz]e|improve|support|contribute|participate|deliver|"
    r"manage|architect|write|automate|deploy|operate|mentor|define|ensure|identify|analy[sz]e|"
    r"evaluate|integrate|migrate|scale|model|help|establish|enable|translate|review|document|"
    r"champion|continuously)(?:s|es|d|ed|ing)?\b",
    re.I,
)
_INLINE = re.compile(
    r"^\W*(must[- ]have skills?|good[- ]to[- ]have skills?|nice[- ]to[- ]have|required skills?|"
    r"preferred skills?|mandatory skills?)\s*[:\-]\s*(.+)$",
    re.I,
)
_PREFERRED_CUE = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|bonus|a plus|is a plus|are a plus|plus\b|good[- ]to[- ]have|"
    r"desirable|ideally|advantageous|beneficial|would be great)\b",
    re.I,
)
_REQUIRED_CUE = re.compile(
    r"\b(required|must|minimum|at least|essential|mandatory|proven|strong (?:experience|"
    r"knowledge|proficiency)|\d+\+?\s*(?:years?|yrs))\b",
    re.I,
)
_ANY_CUE = re.compile(
    r"\b(such as|e\.g\.?|eg\.|like|including|or|any of|one or more|at least one|similar|"
    r"equivalent|and/or|for example)\b",
    re.I,
)
_YEARS = re.compile(
    r"(?:minimum (?:of )?|at least |over )?(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:(?:-|\u2013|to)\s*"
    r"(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)(?:\s*\(s\))?",
    re.I,
)
_EDUCATION = re.compile(
    r"\b(bachelor'?s?|master'?s?|b\.?\s?tech|b\.?\s?e\b|m\.?\s?tech|b\.?\s?s\.?\b|m\.?\s?s\.?\b|"
    r"ph\.?\s?d|degree|graduate|diploma|computer science|full[- ]time education)\b",
    re.I,
)
_LEADERSHIP = (
    ("mentoring", re.compile(r"\bmentor(?:ing|s|ship)?\b", re.I)),
    (
        "team leadership",
        re.compile(
            r"\blead(?:ing)? (?:a |the )?(?:team|engineers|squad)\b|"
            r"\bpeople management\b|\bmanage (?:a |the )?team\b",
            re.I,
        ),
    ),
    (
        "technical leadership",
        re.compile(
            r"\btech(?:nical)? lead(?:ership)?\b|\bset technical "
            r"direction\b|\barchitect(?:ural)? decisions?\b",
            re.I,
        ),
    ),
    (
        "ownership",
        re.compile(
            r"\bown(?:ership| the| end[- ]to[- ]end)?\b|\bend[- ]to[- ]end\b|"
            r"\baccountab",
            re.I,
        ),
    ),
    (
        "cross-team influence",
        re.compile(
            r"\bstakeholders?\b|\bcross[- ]functional\b|"
            r"\binfluence\b|\bpartner with\b",
            re.I,
        ),
    ),
    ("driving initiatives", re.compile(r"\b(?:drive|drives|driving|champion)\b", re.I)),
)
_SENIORITY_TITLE = (
    ("intern", re.compile(r"\b(intern(?:ship)?|trainee|apprentice)\b", re.I)),
    (
        "executive",
        re.compile(r"\b(director|head of|vice president|svp|evp|vp\b|chief|cto|cdo)\b", re.I),
    ),
    ("manager", re.compile(r"\bmanager\b", re.I)),
    ("principal", re.compile(r"\b(principal|distinguished|fellow)\b", re.I)),
    ("staff", re.compile(r"\b(staff|lead|architect)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|iii\b|level\s*3)\b", re.I)),
    ("mid", re.compile(r"\b(ii\b|level\s*2|mid[- ]level)\b", re.I)),
    (
        "junior",
        re.compile(r"\b(junior|jr\.?|associate|entry[- ]level|graduate|new grad|i\b)\b", re.I),
    ),
)
SENIORITY_LEVEL = {
    "intern": 0, "junior": 1, "mid": 2, "senior": 3, "staff": 4, "principal": 5, "manager": 4,
    "executive": 6, "unknown": None,
}  # fmt: skip
PEOPLE_MANAGEMENT_LEVELS = {"manager", "executive"}
# Banks use (A)VP as a corporate title for senior individual contributors
# ("Data Engineer - Vice President"); without an IC role noun it is an executive title.
_CORPORATE_TITLE = re.compile(r"\b(assistant vice president|avp|vice president|vp)\b", re.I)
_IC_ROLE_NOUN = re.compile(r"\b(engineer|developer|architect|scientist|programmer)\b", re.I)
_REMOTE = re.compile(r"\bremote\b|\bwork from (?:home|anywhere)\b|\bwfh\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ONSITE = re.compile(
    r"\bon[- ]?site\b|\bin[- ]office\b|\bbased (?:at|in|out of) our\b|"
    r"\bwork from (?:the )?office\b|\b\d days? (?:a|per) week in (?:the )?office\b",
    re.I,
)
_RELOCATION = re.compile(r"\brelocat(?:e|ion)\b", re.I)
_SPONSOR = re.compile(r"\b(?:visa )?sponsorship\b|\bsponsor\b", re.I)
_SALARY = re.compile(
    r"(?P<cur>[$\u20b9\u00a3\u20ac]|usd|inr|rs\.?|gbp|eur|cad|sgd)\s?(?P<lo>\d[\d,]*(?:\.\d+)?)"
    r"\s?(?P<lo_unit>k|lpa|lakhs?|l|cr|m)?\s?(?:-|\u2013|to)\s?(?:[$\u20b9\u00a3\u20ac]|usd|inr)?"
    r"\s?(?P<hi>\d[\d,]*(?:\.\d+)?)\s?(?P<hi_unit>k|lpa|lakhs?|l|cr|m)?",
    re.I,
)
_ANALYST = re.compile(
    r"\b(dashboards?|tableau|power ?bi|looker|kpis?|business insights|"
    r"statistical analysis|reporting|a/b test\w*|visuali[sz]ation)\b",
    re.I,
)
_COUNTRY_HINTS = {
    "United States": re.compile(
        r"\b(united states|usa|u\.s\.a?\.?|us(?=[\s,;-]|$)|"
        r"seattle|san francisco|new york|austin|santa clara|bellevue|"
        r"menlo park|mountain view|sunnyvale|boston|chicago|atlanta|"
        r"denver|redmond|san jose|los angeles|washington, dc)\b",
        re.I,
    ),
    "United Kingdom": re.compile(
        r"\b(united kingdom|uk|london|manchester|edinburgh|"
        r"cambridge, uk)\b",
        re.I,
    ),
    "Ireland": re.compile(r"\b(ireland|dublin|irl)\b", re.I),
    "Canada": re.compile(r"\b(canada|toronto|vancouver|montreal|ontario)\b", re.I),
    "Germany": re.compile(r"\b(germany|berlin|munich|deu)\b", re.I),
    "Singapore": re.compile(r"\b(singapore|sgp)\b", re.I),
    "Japan": re.compile(r"\b(japan|tokyo|jpn)\b", re.I),
    "Brazil": re.compile(r"\b(brazil|bra|s[aã]o paulo)\b", re.I),
    "Israel": re.compile(r"\b(israel|isr|tel aviv)\b", re.I),
    "Netherlands": re.compile(r"\b(netherlands|amsterdam)\b", re.I),
    "Poland": re.compile(r"\b(poland|warsaw|krak[oó]w)\b", re.I),
    "Australia": re.compile(r"\b(australia|sydney|melbourne)\b", re.I),
    "Spain": re.compile(r"\b(spain|madrid|barcelona)\b", re.I),
    "France": re.compile(r"\b(france|paris)\b", re.I),
    "Mexico": re.compile(r"\b(mexico|guadalajara)\b", re.I),
    "Costa Rica": re.compile(r"\b(costa rica)\b", re.I),
    "Romania": re.compile(r"\b(romania|bucharest)\b", re.I),
    "Philippines": re.compile(r"\b(philippines|manila)\b", re.I),
}


@dataclass
class Requirement:
    id: str
    text: str
    kind: str  # required | preferred | responsibility | context
    technologies: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    mode: str = "all"
    years: float | None = None
    education: bool = False
    importance: float = 1.0


@dataclass
class JDAnalysisResult:
    description_hash: str
    analyzer_version: str
    seniority: str
    years_min: float | None
    years_max: float | None
    requirements: list[dict[str, Any]]
    required_skills: list[dict[str, Any]]
    preferred_skills: list[dict[str, Any]]
    responsibilities: list[str]
    categories: dict[str, list[str]]
    domains: list[str]
    leadership_signals: list[str]
    keywords: list[str]
    business_context: str | None
    location_requirements: dict[str, Any]
    compensation: dict[str, Any] | None
    education: list[str]
    role_focus: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


IMPORTANCE = {"required": 1.0, "responsibility": 0.7, "preferred": 0.45, "context": 0.25}


def description_hash(title: str, description: str) -> str:
    normalized = re.sub(r"\s+", " ", f"{title}\n{description}".strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _heading_kind(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 70:
        return None
    # A heading is a short line, often ending with ":"; a bullet or full sentence is not.
    if re.match(r"^\s*[-\u2022*\u25cf\u25e6]", stripped) or re.search(r"[.!?]$", stripped):
        return None
    for kind, pattern in _HEADINGS:
        if pattern.match(stripped.rstrip(":")):
            return kind
    return None


def _split_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        text = re.sub(r"^\s*(?:[-\u2022*\u25cf\u25e6\u25aa\u2013]|\d+[.)])\s*", "", line).strip()
        if not text:
            continue
        if len(text) > 260:
            items.extend(
                part.strip() for part in re.split(r"(?<=[.;])\s+(?=[A-Z])", text) if part.strip()
            )
        else:
            items.append(text)
    return items


def _sections(description: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = [("intro", [])]
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        inline = _INLINE.match(line)
        if inline:
            label = inline.group(1).lower()
            kind = "preferred" if re.search(r"good|nice|preferred", label) else "required"
            value = inline.group(2).strip()
            if value.upper() not in {"NA", "N/A", "NONE", "-"}:
                sections.append((kind, [value]))
                sections.append((sections[-2][0] if len(sections) > 1 else "intro", []))
            continue
        # "Heading: content" on one line ("Role: Data Engineer" is a label, not a section).
        split = re.match(r"^([A-Za-z][A-Za-z '&/\u2019-]{2,60}):\s+(\S.+)$", line)
        if (
            split
            and _heading_kind(split.group(1))
            and (len(split.group(2).split()) >= 4 or "," in split.group(2))
        ):
            sections.append((_heading_kind(split.group(1)) or "intro", [split.group(2)]))
            continue
        kind = _heading_kind(line)
        if kind:
            sections.append((kind, []))
        else:
            sections[-1][1].append(line)
    return [(kind, lines) for kind, lines in sections if lines]


def _mode(text: str, hits: list[Any], term_count: int) -> str:
    if term_count <= 1:
        return "all"
    if _ANY_CUE.search(text):
        return "any"
    # "Kafka/Kinesis": alternatives joined by a slash.
    techs = [hit for hit in hits if hit.kind == "tech"]
    for first, second in zip(techs, techs[1:], strict=False):
        between = text[first.start + len(first.text) : second.start]
        if re.fullmatch(r"\s*/\s*", between):
            return "any"
    return "all"


def _years(text: str) -> tuple[float | None, float | None]:
    if not re.search(
        r"\b(experience|exp\b|years? of|background|working)", text, re.I
    ) and not re.search(r"\d+\s*\+\s*(?:years?|yrs)", text, re.I):
        return None, None
    match = _YEARS.search(text)
    if not match:
        return None, None
    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) else None
    if low > 30:
        return None, None
    return low, high


def _location_requirements(location: str, remote_status: str, text: str) -> dict[str, Any]:
    places = [part.strip() for part in re.split(r";|\|| / ", location or "") if part.strip()]
    countries: list[str] = []
    india = False
    for place in places or [location or ""]:
        if is_india_location(place):
            india = True
            if "India" not in countries:
                countries.append("India")
            continue
        for country, pattern in _COUNTRY_HINTS.items():
            if pattern.search(place) and country not in countries:
                countries.append(country)
    remote_regions: list[str] = []
    for place in places:
        if _REMOTE.search(place):
            region = re.sub(r"(?i)\bremote\b|[-,()]", " ", place).strip()
            remote_regions.append(region or "unspecified")
    if remote_status == "remote" or remote_regions:
        mode = "remote"
    elif remote_status == "hybrid" or _HYBRID.search(text[:4000]):
        mode = "hybrid"
    elif remote_status == "onsite" or _ONSITE.search(text):
        mode = "onsite"
    else:
        mode = "unknown"
    relocation = None
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _RELOCATION.search(sentence):
            relocation = not re.search(r"\b(no|not)\b", sentence, re.I)
            break
    return {
        "mode": mode,
        "places": places[:12],
        "countries": countries,
        "india": india,
        "remote_regions": remote_regions,
        "relocation_offered": relocation,
        "visa_sponsorship_mentioned": bool(_SPONSOR.search(text)),
    }


def _money(value: str, unit: str | None) -> float:
    number = float(value.replace(",", ""))
    unit = (unit or "").lower()
    if unit == "k":
        number *= 1_000
    elif unit in {"lpa", "lakh", "lakhs", "l"}:
        number *= 100_000
    elif unit == "cr":
        number *= 10_000_000
    elif unit == "m":
        number *= 1_000_000
    return number


def _compensation(salary: str | None, text: str) -> dict[str, Any] | None:
    for source in (salary or "", text):
        match = _SALARY.search(source)
        if not match:
            continue
        currency = match.group("cur").upper().replace("$", "USD").replace("\u20b9", "INR")
        currency = {"\u00a3": "GBP", "\u20ac": "EUR", "RS": "INR", "RS.": "INR"}.get(
            currency, currency
        )
        low = _money(match.group("lo"), match.group("lo_unit") or match.group("hi_unit"))
        high = _money(match.group("hi"), match.group("hi_unit"))
        if high < low or low <= 0:
            continue
        window = source[max(0, match.start() - 80) : match.end() + 80].lower()
        period = "hour" if re.search(r"\bhour|/hr\b", window) else "year"
        return {
            "min": low,
            "max": high,
            "currency": currency,
            "period": period,
            "text": source[match.start() : match.end()],
        }
    return None


def _title_seniority(title: str) -> str | None:
    corporate = _CORPORATE_TITLE.search(title)
    if (
        corporate
        and _IC_ROLE_NOUN.search(title)
        and not re.search(r"\b(director|head of|chief|manager)\b", title, re.I)
    ):
        return "senior" if corporate.group(0).lower().startswith(("avp", "assistant")) else "staff"
    for level, pattern in _SENIORITY_TITLE:
        if pattern.search(title):
            return level
    return None


def _years_seniority(years: float | None) -> str:
    if years is None:
        return "unknown"
    if years < 2:
        return "junior"
    if years < 5:
        return "mid"
    if years < 8:
        return "senior"
    if years < 12:
        return "staff"
    return "principal"


def analyze_job(
    title: str,
    description: str,
    *,
    location: str = "",
    remote_status: str = "unknown",
    salary: str | None = None,
    company_name: str | None = None,
) -> JDAnalysisResult:
    warnings: list[str] = []
    own_key = company_key(company_name or "")
    sections = _sections(description or "")
    requirements: list[Requirement] = []
    counters = Counter()
    responsibilities: list[str] = []
    context_lines: list[str] = []
    education: list[str] = []
    seen: set[str] = set()
    for kind, lines in sections:
        if kind == "ignore":
            continue
        items = _split_items(lines)
        if kind in {"intro", "context"}:
            context_lines.extend(items[:6])
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            effective = kind
            if kind in {"intro", "context"}:
                if _PREFERRED_CUE.search(item):
                    effective = "preferred"
                elif _REQUIRED_CUE.search(item) and re.search(
                    r"\b(experience|knowledge|proficien|skills?|familiar)", item, re.I
                ):
                    effective = "required"
                elif kind == "intro" and _REQUIREMENT_START.search(item):
                    effective = "required"
                elif kind == "intro" and _RESPONSIBILITY_START.search(item):
                    effective = "responsibility"
                else:
                    effective = "context"
            elif (
                kind == "required"
                and _PREFERRED_CUE.search(item)
                and not _REQUIRED_CUE.search(item)
            ):
                effective = "preferred"
            hits = find_terms(item)
            techs = [
                hit.name for hit in hits if hit.kind == "tech" and company_key(hit.name) != own_key
            ]
            concepts = [hit.name for hit in hits if hit.kind == "concept"]
            if techs and any(company_key(hit.name) == own_key for hit in hits):
                warnings.append(f"'{company_name}' is the hiring company, not treated as a skill.")
            years_low, _years_high = _years(item)
            is_education = bool(_EDUCATION.search(item)) and bool(
                re.search(r"degree|bachelor|master|b\.?tech|computer science|education", item, re.I)
            )
            if is_education:
                education.append(item)
            if effective == "responsibility":
                responsibilities.append(item)
            if not (techs or concepts or years_low is not None or is_education):
                continue
            if effective == "context" and not techs:
                continue
            prefix = {"required": "R", "preferred": "P", "responsibility": "D", "context": "C"}[
                effective
            ]
            counters[prefix] += 1
            requirements.append(
                Requirement(
                    id=f"{prefix}{counters[prefix]}",
                    text=item[:500],
                    kind=effective,
                    technologies=techs,
                    concepts=concepts,
                    mode=_mode(
                        item,
                        hits,
                        len(techs) + len([c for c in concepts if c not in WEAK_CONCEPTS]),
                    ),
                    years=years_low,
                    education=is_education,
                    importance=IMPORTANCE[effective],
                )
            )

    skill_weight: dict[str, float] = {}
    skill_kinds: dict[str, set[str]] = {}
    skill_requirements: dict[str, list[str]] = {}
    for requirement in requirements:
        for name in requirement.technologies + requirement.concepts:
            weight = requirement.importance * (0.6 if name in WEAK_CONCEPTS else 1.0)
            skill_weight[name] = skill_weight.get(name, 0.0) + weight
            skill_kinds.setdefault(name, set()).add(requirement.kind)
            skill_requirements.setdefault(name, []).append(requirement.id)
    required_skills: list[dict[str, Any]] = []
    preferred_skills: list[dict[str, Any]] = []
    for name, weight in sorted(skill_weight.items(), key=lambda item: -item[1]):
        entry = {
            "name": name,
            "kind": kind_of(name),
            "weight": round(weight, 2),
            "requirement_ids": skill_requirements[name],
        }
        if skill_kinds[name] & {"required", "responsibility"}:
            required_skills.append(entry)
        else:
            preferred_skills.append(entry)

    years_candidates = [
        requirement.years
        for requirement in requirements
        if requirement.kind == "required" and requirement.years
    ] or [requirement.years for requirement in requirements if requirement.years]
    years_min = max(years_candidates) if years_candidates else None
    years_max = None
    for requirement in requirements:
        if requirement.years == years_min:
            _low, years_max = _years(requirement.text)
            break
    # A range in the title ("Data Engineer (1 - 2 years of experience)") is the headline ask.
    title_low, title_high = _years(title or "")
    if title_low is not None:
        years_min, years_max = title_low, title_high
    seniority = _title_seniority(title or "") or _years_seniority(years_min)
    if seniority == "unknown" and years_min is None:
        warnings.append("Seniority could not be determined from the title or required years.")

    text = description or ""
    leadership = [label for label, pattern in _LEADERSHIP if pattern.search(text)]
    all_terms = [name for name, _weight in sorted(skill_weight.items(), key=lambda item: -item[1])]
    keywords = [name for name in all_terms if name not in WEAK_CONCEPTS][:25]
    analyst_hits = len({match.lower() for match in _ANALYST.findall(text)})
    data_terms = [
        name
        for name in expand_implied(all_terms)
        if name
        in {
            "Spark",
            "Kafka",
            "Airflow",
            "Data pipelines / ETL",
            "Data warehousing",
            "Streaming / real-time processing",
            "Data lake / lakehouse",
            "Change data capture (CDC)",
            "Data platform engineering",
            "dbt",
            "Flink",
        }
    ]
    platform_terms = [
        name
        for name in all_terms
        if name
        in {
            "Kubernetes",
            "Terraform",
            "Infrastructure as code",
            "CI/CD & DevOps",
            "Containers & orchestration",
            "Observability & monitoring",
        }
    ]
    ml_terms = [name for name in all_terms if name == "Machine learning & features"]
    if analyst_hits >= 3 and len(data_terms) < 3:
        role_focus = "analytics"
    elif len(platform_terms) >= 3 and len(platform_terms) > len(data_terms):
        role_focus = "platform"
    elif data_terms:
        role_focus = "data_engineering"
    elif ml_terms:
        role_focus = "ml"
    else:
        role_focus = "software"
    business_context = " ".join(context_lines[:2])[:600] or None
    domain_text = " ".join(context_lines + responsibilities + [r.text for r in requirements])
    return JDAnalysisResult(
        description_hash=description_hash(title or "", description or ""),
        analyzer_version=ANALYZER_VERSION,
        seniority=seniority,
        years_min=years_min,
        years_max=years_max,
        requirements=[asdict(requirement) for requirement in requirements],
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        responsibilities=responsibilities[:20],
        categories=group_terms(all_terms),
        domains=find_domains(domain_text, emphasis=title, minimum=2),
        leadership_signals=leadership,
        keywords=keywords,
        business_context=business_context,
        location_requirements=_location_requirements(location, remote_status, text),
        compensation=_compensation(salary, text),
        education=education[:5],
        role_focus=role_focus,
        warnings=list(dict.fromkeys(warnings)),
    )
