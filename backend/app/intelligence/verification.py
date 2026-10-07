"""Claim verification: every resume statement must be supported by candidate facts.

A claim passes only if its technologies, numbers, leadership verbs, production claims and
seniority words are present in (or implied by) the facts it cites. Text copied from the job
description cannot manufacture qualifications.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.intelligence.taxonomy import (
    expand_implied,
    find_metrics,
    find_terms,
    kind_of,
    normalize_metric,
)

_LEAD_VERBS = re.compile(
    r"\b(led|lead|leading|spearhead(?:ed|ing)?|head(?:ed|ing)|direct(?:ed|ing)|manag(?:ed|ing)|"
    r"own(?:ed|ing)|architect(?:ed|ing)|drove|driv(?:e|ing)|champion(?:ed|ing)|oversaw|"
    r"mentor(?:ed|ing))\b",
    re.I,
)
_SENIOR_WORDS = re.compile(
    r"\b(senior|sr\.?|lead|staff|principal|architect|manager|head|director)\b", re.I
)
_PRODUCTION = re.compile(r"\bproduction(?:-grade)?\b", re.I)
_STOPWORDS = set(
    "a an and the of for to in on at by with from into over under using via across as is are was "
    "were be been this that these those its it their our your my we i you they he she or not no "
    "while when where which who whom whose than then so such also more most less least per each "
    "all any both data".split()
)
# Concepts whose presence in a generated claim must be backed by the cited facts.
_RISKY_CONCEPTS = {
    "Leadership & mentoring",
    "System design & architecture",
    "Machine learning & features",
    "Data modeling",
    "Data warehousing",
    "Data governance & compliance",
    "Infrastructure as code",
    "Containers & orchestration",
    "Distributed systems",
    "Data lake / lakehouse",
    "Streaming / real-time processing",
    "Change data capture (CDC)",
    "Low-latency serving",
    "Caching",
    "CI/CD & DevOps",
    "Workflow orchestration",
    "Analytics & BI reporting",
    "Data migration",
}


@dataclass
class VerificationResult:
    ok: bool
    notes: list[str] = field(default_factory=list)
    unsupported_terms: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)


def _source_text(sources: Iterable[Any]) -> str:
    parts: list[str] = []
    for fact in sources:
        label = getattr(fact, "label", None)
        parts.append(f"{label}: {fact.statement}" if label else fact.statement)
        for extra in ("role_title", "employer"):
            value = getattr(fact, extra, None)
            if value:
                parts.append(value)
    return "\n".join(parts)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#./%-]+", text.lower())


def _copied_from_jd(claim: str, jd_text: str, source_text: str, size: int = 8) -> str | None:
    claim_words = _words(claim)
    if len(claim_words) < size:
        return None
    jd = " ".join(_words(jd_text))
    source = " ".join(_words(source_text))
    for start in range(0, len(claim_words) - size + 1):
        gram = " ".join(claim_words[start : start + size])
        if gram in jd and gram not in source:
            return gram
    return None


def verify_claim(
    text: str,
    sources: list[Any],
    *,
    kind: str = "generated",
    allowed_terms: Iterable[str] = (),
    allowed_numbers: Iterable[str] = (),
    candidate_titles: Iterable[str] = (),
    jd_text: str | None = None,
    max_length_ratio: float | None = None,
) -> VerificationResult:
    """Check ``text`` against the facts it cites.

    ``kind`` is "verbatim" when the text is a fact's own wording (only reordering or alias
    normalisation applied), "generated" for composed or rewritten text.
    """
    notes: list[str] = []
    if not sources and kind != "header":
        return VerificationResult(False, ["No source fact is cited."])
    source_text = _source_text(sources)
    source_terms: set[str] = set()
    for fact in sources:
        source_terms.update(getattr(fact, "technologies", None) or [])
        source_terms.update(getattr(fact, "concepts", None) or [])
    source_terms.update(term.name for term in find_terms(source_text))
    supported = expand_implied(source_terms) | set(allowed_terms)

    claim_terms = find_terms(text)
    unsupported_terms = [
        hit.name for hit in claim_terms if hit.kind == "tech" and hit.name not in supported
    ]
    if kind != "verbatim":
        unsupported_terms.extend(
            hit.name
            for hit in claim_terms
            if hit.kind == "concept" and hit.name in _RISKY_CONCEPTS and hit.name not in supported
        )
    if unsupported_terms:
        notes.append(
            "Not supported by the cited facts: " + ", ".join(sorted(set(unsupported_terms))) + "."
        )

    source_numbers = {normalize_metric(metric) for metric in find_metrics(source_text)}
    source_numbers |= {normalize_metric(value) for value in allowed_numbers}
    unsupported_numbers = [
        metric for metric in find_metrics(text) if normalize_metric(metric) not in source_numbers
    ]
    if unsupported_numbers:
        notes.append("Numbers not in the cited facts: " + ", ".join(unsupported_numbers) + ".")

    ok = not unsupported_terms and not unsupported_numbers
    if kind != "verbatim":
        lead_verb = _LEAD_VERBS.search(text)
        if lead_verb and not _LEAD_VERBS.search(source_text):
            ok = False
            notes.append(
                f"Uses a leadership/ownership verb ('{lead_verb.group(0)}') that the cited facts "
                "do not use."
            )
        if _PRODUCTION.search(text) and not (
            _PRODUCTION.search(source_text)
            or any(getattr(fact, "experience_type", "") == "PRODUCTION" for fact in sources)
        ):
            ok = False
            notes.append("Claims production experience the cited facts do not show.")
        titles = " ".join(candidate_titles)
        for word in {match.group(0).lower() for match in _SENIOR_WORDS.finditer(text)}:
            if not re.search(rf"\b{re.escape(word)}\b", f"{titles}\n{source_text}", re.I):
                ok = False
                notes.append(f"Seniority word '{word}' does not appear in your titles or facts.")
        if jd_text:
            copied = _copied_from_jd(text, jd_text, source_text)
            if copied:
                ok = False
                notes.append(f"Copies job-description wording not found in your facts: '{copied}'.")
        if max_length_ratio and len(text) > max_length_ratio * max(1, len(source_text)):
            ok = False
            notes.append("Rewrite is much longer than the source fact.")
    return VerificationResult(ok, notes, sorted(set(unsupported_terms)), unsupported_numbers)


def tech_names(text: str) -> list[str]:
    return [hit.name for hit in find_terms(text, kinds=("tech",)) if kind_of(hit.name) == "tech"]
