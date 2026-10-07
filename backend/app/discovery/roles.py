"""Role classification for data-engineering job targeting.

This is a deterministic concept matcher: it combines title patterns with technical
evidence from the description (pipelines, streaming, warehouses, distributed systems)
and counter-evidence (analyst reporting, data-center facilities, hardware). It is not an
embedding model; every decision is returned with human-readable reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CATEGORIES = {"data_engineering", "platform_infrastructure"}


@dataclass(frozen=True)
class RoleClassification:
    category: str
    relevance: float
    reasons: tuple[str, ...]
    signals: tuple[str, ...] = ()

    @property
    def is_target(self) -> bool:
        return self.category in TARGET_CATEGORIES and self.relevance >= 0.5


def _patterns(items: list[tuple[str, float, str]]) -> list[tuple[re.Pattern[str], float, str]]:
    return [(re.compile(pattern, re.I), weight, label) for pattern, weight, label in items]


_FUNCTION_EXCLUSIONS = re.compile(
    r"\b(sales|account\s+(?:executive|manager)|recruit(?:er|ing)|talent\s+acquisition|"
    r"marketing|copywriter|designer|counsel|attorney|lawyer|accountant|nurse|physician|"
    r"driver|technician|mechanic|electrician|warehouse\s+associate|customer\s+(?:success|"
    r"support)|support\s+specialist|pre-?sales|solutions?\s+consultant|"
    r"product\s+(?:delivery\s+)?manager|product\s+owner|program\s+manager|project\s+manager|"
    r"delivery\s+manager|scrum\s+master|business\s+analyst)\b",
    re.I,
)
# Enterprise-application developer titles ("Salesforce Platform Developer") are not data or
# infrastructure engineering even when they say "platform".
_ENTERPRISE_APPS = re.compile(
    r"\b(salesforce|servicenow|sap|dynamics\s*365|oracle\s+(?:apex|ebs|fusion)|workday\s+hcm|"
    r"guidewire|pega|power\s*(?:platform|apps|automate)|sharepoint)\b",
    re.I,
)
# Engineering specialties that share words like "platform" or "infrastructure" with target
# titles ("Network Infrastructure Engineer", "Robotics Platform") but are different jobs.
_NON_DATA_SPECIALTIES = re.compile(
    r"\b(security|cyber\s*security|network(?:ing)?|embedded|firmware|robotics?|autonomous|"
    r"graphics|gpu|kernel|compilers?|mobile|android|ios|front[-\s]?end|ui|ux|games?|gaming|"
    r"audio|video|camera|perception|computer\s+vision|wireless|5g|telecom|voice|desktop|"
    r"end[-\s]?user|endpoint|citrix|vmware|mainframe|cobol|qa|sdet|test|testing|"
    r"verification|validation)\b",
    re.I,
)
_HARD_NON_TARGET = _patterns(
    [
        (r"\bdata\s+cent(?:er|re)s?\b", 0.05, "data-center facilities role"),
        (r"\bdata\s+entry\b", 0.02, "data-entry role"),
        (
            r"\b(?:mechanical|electrical|civil|chemical|hardware|asic|fpga|rf|analog|layout|"
            r"silicon)\s+(?:product\s+)?(?:design\s+)?engineer",
            0.05,
            "hardware or physical engineering role",
        ),
    ]
)
_MASTER_DATA = re.compile(r"\bmaster\s+data\b|\bsap\b", re.I)
_DATA_ENGINEERING_TITLES = _patterns(
    [
        (r"\bdata\s+engineer(?:ing)?\b", 0.95, "data engineer title"),
        (r"\bbig\s+data\b", 0.9, "big data title"),
        (r"\bdata\s+platform\b", 0.95, "data platform title"),
        (r"\bdata\s+infra(?:structure)?\b", 0.95, "data infrastructure title"),
        (r"\bdata\s+systems?\s+(?:engineer|developer)", 0.9, "data systems title"),
        (r"\b(?:etl|elt)\b", 0.8, "ETL/ELT title"),
        (r"\bdata\s+pipelines?\b", 0.85, "data pipeline title"),
        (r"\bdata\s+(?:warehouse|warehousing|lake|lakehouse)\b", 0.8, "data warehouse title"),
        (r"\bstreaming\s+(?:data\s+)?(?:engineer|platform)", 0.8, "streaming title"),
        (
            r"\b(?:software|backend|back-end)\s+(?:development\s+)?engineer\b.{0,40}\bdata\b",
            0.85,
            "software engineer for data",
        ),
        (r"\bdata\b.{0,25}\b(?:software|backend)\s+engineer\b", 0.8, "data software engineer"),
    ]
)
_PLATFORM_TITLES = _patterns(
    [
        (r"\bdistributed\s+systems?\b", 0.9, "distributed systems title"),
        (r"\bplatform\s+engineer", 0.75, "platform engineer title"),
        (r"\binfra(?:structure)?\s+(?:software\s+)?engineer", 0.75, "infrastructure title"),
        (
            r"\b(?:software|backend)\s+engineer\b.{0,40}\b(?:infra(?:structure)?|platform)\b",
            0.8,
            "software engineer for infrastructure",
        ),
        # Adjacent operations roles: tracked, but below the 0.5 target threshold.
        (r"\bsite\s+reliability\b|\bsre\b", 0.45, "site reliability title"),
        (r"\bcloud\s+(?:platform\s+|infrastructure\s+)?engineer", 0.45, "cloud engineer title"),
        (r"\bdevops\b", 0.45, "DevOps title"),
        (r"\bdatabase\s+(?:engineer|reliability|administrator)", 0.5, "database title"),
    ]
)
_ADJACENT_TITLES = _patterns(
    [
        (r"\bdata\s+analy(?:st|tics)\b", 0.15, "data analyst role, not data engineering"),
        (r"\b(?:product|marketing|business|growth)\s+analy(?:st|tics)\b", 0.15, "analytics role"),
        (
            r"\bbusiness\s+(?:intelligence|analyst)\b|\bbi\s+(?:developer|analyst|engineer)\b",
            0.2,
            "business intelligence role",
        ),
        (r"\banalytics\s+engineer", 0.55, "analytics engineering (adjacent)"),
        (r"\bdata\s+scien(?:ce|tist)\b", 0.3, "data science role"),
        (r"\b(?:ml|machine\s+learning|ai)\s+engineer", 0.35, "machine-learning engineer"),
        (
            r"\bdata\s+(?:governance|quality\s+analyst|steward|privacy|protection)\b",
            0.25,
            "data governance role",
        ),
    ]
)
_GENERIC_ENGINEERING = re.compile(
    r"\b(?:software|backend|back-end|full[\s-]?stack|java|python|scala|golang|go)\s+"
    r"(?:engineer|developer)\b|\bsde\b|\bswe\b|\bmember of technical staff\b",
    re.I,
)
_ANY_ENGINEERING = re.compile(r"\b(engineer|developer|architect|programmer)\b", re.I)

_DATA_SIGNALS = _patterns(
    [
        (r"\bpy?spark\b", 1, "Spark"),
        (r"\bkafka\b", 1, "Kafka"),
        (r"\bairflow\b", 1, "Airflow"),
        (r"\bdbt\b", 1, "dbt"),
        (r"\bflink\b", 1, "Flink"),
        (r"\bapache\s+beam\b", 1, "Beam"),
        (r"\bdatabricks\b", 1, "Databricks"),
        (r"\bsnowflake\b", 1, "Snowflake"),
        (r"\bbigquery\b", 1, "BigQuery"),
        (r"\bredshift\b", 1, "Redshift"),
        (r"\bhadoop\b|\bhdfs\b", 1, "Hadoop"),
        (r"\bhive\b", 1, "Hive"),
        (r"\bpresto\b|\btrino\b", 1, "Presto/Trino"),
        (r"\biceberg\b|\bdelta\s+lake\b|\bhudi\b", 1, "table formats"),
        (r"\betl\b|\belt\b", 1, "ETL/ELT"),
        (r"\bdata\s+pipelines?\b", 1, "data pipelines"),
        (r"\bdata\s+lakes?\b|\blakehouse\b", 1, "data lake"),
        (r"\bdata\s+warehous(?:e|es|ing)\b", 1, "data warehouse"),
        (r"\bdata\s+model(?:l)?ing\b", 1, "data modeling"),
        (r"\bstream(?:ing)?\s+(?:data|processing)\b|\breal[-\s]time\s+data\b", 1, "streaming"),
        (r"\bbatch\s+processing\b", 1, "batch processing"),
        (r"\bchange\s+data\s+capture\b|\bcdc\b", 1, "CDC"),
        (r"\bparquet\b|\bavro\b", 1, "columnar formats"),
        (
            r"\b(?:aws\s+glue|emr|dataflow|dataproc|synapse|data\s+factory|kinesis)\b",
            1,
            "cloud data services",
        ),
        (r"\bdata\s+ingestion\b", 1, "data ingestion"),
        (r"\bdata\s+(?:platform|infrastructure)\b", 1, "data platform"),
        (r"\b(?:petabyte|terabyte)s?\b", 1, "large-scale data"),
    ]
)
_CORE_DATA_SIGNALS = {
    "Spark", "Kafka", "Airflow", "dbt", "Flink", "Beam", "ETL/ELT", "data pipelines",
    "data lake", "data warehouse", "streaming", "CDC", "data ingestion", "data platform",
}  # fmt: skip
_PLATFORM_SIGNALS = _patterns(
    [
        (r"\bkubernetes\b|\bk8s\b", 1, "Kubernetes"),
        (r"\bterraform\b|\binfrastructure\s+as\s+code\b", 1, "infrastructure as code"),
        (r"\bdistributed\s+systems?\b", 1, "distributed systems"),
        (r"\bmicroservices?\b", 1, "microservices"),
        (r"\bobservability\b", 1, "observability"),
        (r"\bservice\s+mesh\b", 1, "service mesh"),
        (r"\bhigh\s+availability\b|\bfault[-\s]toleran", 1, "fault tolerance"),
        (r"\bconsensus\b|\braft\b|\bpaxos\b", 1, "consensus"),
    ]
)
_ANALYST_SIGNALS = re.compile(
    r"\b(dashboards?|tableau|power\s*bi|looker|excel|reporting|kpis?|stakeholders?|insights|"
    r"a/b\s+test\w*|visuali[sz]ations?|storytelling|ad[-\s]hoc\s+analysis)\b",
    re.I,
)
_HARDWARE_SIGNALS = re.compile(
    r"\b(interconnect|cables?|pcb|silicon|asic|racks?|cooling|hvac|electrical|mechanical|"
    r"manufacturing|bill of materials|bom|plm|firmware|power\s+distribution|facilit(?:y|ies))\b",
    re.I,
)


def _best(patterns: list[tuple[re.Pattern[str], float, str]], text: str) -> tuple[float, str]:
    best: tuple[float, str] = (0.0, "")
    for pattern, weight, label in patterns:
        if pattern.search(text) and weight > best[0]:
            best = (weight, label)
    return best


def _signals(patterns: list[tuple[re.Pattern[str], float, str]], text: str) -> list[str]:
    return [label for pattern, _weight, label in patterns if pattern.search(text)]


def classify_role(
    title: str | None, description: str | None = None, company: str | None = None
) -> RoleClassification:
    title = (title or "").strip()
    if not title:
        return RoleClassification("unclassified", 0.0, ("No title available.",))
    description = description or ""
    if company and len(company.strip()) >= 3:
        # "Snowflake" or "Databricks" in their own job descriptions is the employer's name,
        # not evidence of data-engineering technology.
        description = re.sub(rf"\b{re.escape(company.strip())}\b", " ", description, flags=re.I)
    text = f"{title}\n{description}"
    has_description = len(description) >= 150
    data_signals = _signals(_DATA_SIGNALS, text)
    platform_signals = _signals(_PLATFORM_SIGNALS, text)
    analyst_count = len(set(match.lower() for match in _ANALYST_SIGNALS.findall(description or "")))
    hardware_count = len(
        set(match.lower() for match in _HARDWARE_SIGNALS.findall(description or ""))
    )
    signals = tuple(dict.fromkeys(data_signals + platform_signals))

    if _FUNCTION_EXCLUSIONS.search(title):
        return RoleClassification(
            "non_target", 0.02, ("Non-engineering function in title.",), signals
        )
    data_weight, data_label = _best(_DATA_ENGINEERING_TITLES, title)
    if _ENTERPRISE_APPS.search(title) and not data_label:
        return RoleClassification(
            "other_engineering",
            0.2,
            ("Enterprise-application role (for example Salesforce or SAP), not data engineering.",),
            signals,
        )
    hard_weight, hard_label = _best(_HARD_NON_TARGET, title)
    if hard_label and not data_label:
        return RoleClassification("non_target", hard_weight, (f"Excluded: {hard_label}.",), signals)
    specialty = _NON_DATA_SPECIALTIES.search(title)
    if specialty and not data_label:
        return RoleClassification(
            "other_engineering",
            0.2,
            (
                f"Specialised {specialty.group(0).lower()} engineering role, not data or "
                "platform engineering.",
            ),
            signals,
        )
    if data_label and _MASTER_DATA.search(title):
        return RoleClassification(
            "adjacent_data",
            0.3,
            ("Master-data/ERP data role, not pipeline or platform data engineering.",),
            signals,
        )

    adjacent_weight, adjacent_label = _best(_ADJACENT_TITLES, title)
    # The role noun comes first ("BI Engineer, Data Engineering Team"); a team name later in
    # the title does not turn an analyst or BI role into data engineering.
    primary = re.split(r"\s*[,|(]\s*|\s+[-\u2013\u2014:]\s+", title, maxsplit=1)[0]
    primary_adjacent = _best(_ADJACENT_TITLES, primary)[1]
    if adjacent_label and (
        not data_label or (primary_adjacent and not _best(_DATA_ENGINEERING_TITLES, primary)[1])
    ):
        relevance = adjacent_weight
        reasons = [f"Title indicates {adjacent_label}."]
        if len(data_signals) >= 4:
            relevance = min(0.65, relevance + 0.1)
            reasons.append(
                f"Description includes data-engineering work: {', '.join(data_signals[:5])}."
            )
        return RoleClassification("adjacent_data", relevance, tuple(reasons), signals)

    if data_label:
        relevance = data_weight
        reasons = [f"Title indicates {data_label}."]
        if has_description and not data_signals and (hardware_count >= 2 or analyst_count >= 3):
            return RoleClassification(
                "adjacent_data",
                0.3,
                (
                    f"Title indicates {data_label}, but the description describes "
                    + ("hardware/product work" if hardware_count >= 2 else "analyst reporting")
                    + " without data-engineering technology.",
                ),
                signals,
            )
        if hard_label:
            relevance -= 0.2
            reasons.append(f"Also mentions {hard_label}.")
        if len(data_signals) >= 3:
            relevance += 0.05
            reasons.append(f"Description evidence: {', '.join(data_signals[:6])}.")
        return RoleClassification(
            "data_engineering", round(min(1.0, relevance), 2), tuple(reasons), signals
        )

    platform_weight, platform_label = _best(_PLATFORM_TITLES, title)
    if platform_label:
        relevance = platform_weight
        reasons = [f"Title indicates {platform_label}."]
        if len(data_signals) >= 3:
            relevance += 0.05
            reasons.append(f"Description includes data systems: {', '.join(data_signals[:5])}.")
        return RoleClassification(
            "platform_infrastructure", round(min(1.0, relevance), 2), tuple(reasons), signals
        )

    if _GENERIC_ENGINEERING.search(title):
        core = set(data_signals) & _CORE_DATA_SIGNALS
        if len(data_signals) >= 4 and core:
            return RoleClassification(
                "data_engineering",
                0.6,
                (
                    f"Engineering role whose description centers on data systems: "
                    f"{', '.join(data_signals[:6])}.",
                ),
                signals,
            )
        if len(platform_signals) >= 3:
            return RoleClassification(
                "platform_infrastructure",
                0.55,
                (
                    "Engineering role with infrastructure focus: "
                    f"{', '.join(platform_signals[:5])}.",
                ),
                signals,
            )
        return RoleClassification(
            "other_engineering", 0.25, ("General engineering role.",), signals
        )
    if _ANY_ENGINEERING.search(title):
        return RoleClassification(
            "other_engineering", 0.2, ("Engineering-related title without a target role.",), signals
        )
    return RoleClassification("non_target", 0.05, ("Not a target engineering role.",), signals)


_ROLE_PHRASE = re.compile(
    r"(?:(?:senior|sr\.?|lead|staff|principal|junior|jr\.?|associate)\s+)?"
    r"(?:big\s+data\s+engineer|data\s+(?:platform|infrastructure|systems)\s+engineer|"
    r"data\s+engineer|distributed\s+systems\s+engineer|platform\s+engineer|"
    r"infrastructure\s+engineer|software\s+engineer[\s,-]+(?:data|infrastructure)|"
    r"etl\s+(?:engineer|developer))",
    re.I,
)


def find_role_phrase(text: str) -> re.Match[str] | None:
    return _ROLE_PHRASE.search(text or "")
