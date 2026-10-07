"""Technology and concept taxonomy used by resume parsing, JD analysis, matching and claim
verification.

Terms are matched with explicit alias patterns (never fuzzy similarity), so every match can be
shown to the user. ``implies`` expands a specific term to the general ones it proves (PySpark
proves Spark and Python); ``family`` groups peers whose experience is *transferable* but never
equivalent (Azure is not AWS).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Term:
    name: str
    kind: str  # "tech" or "concept"
    category: str
    patterns: tuple[str, ...]
    implies: tuple[str, ...] = ()
    family: str | None = None
    case_sensitive: bool = False
    regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        body = "|".join(f"(?:{pattern})" for pattern in self.patterns)
        flags = 0 if self.case_sensitive else re.IGNORECASE
        object.__setattr__(
            self, "regex", re.compile(rf"(?<![-A-Za-z0-9+#])(?:{body})(?![A-Za-z0-9+#])", flags)
        )


def _t(name, category, *patterns, implies=(), family=None, case_sensitive=False) -> Term:
    return Term(name, "tech", category, tuple(patterns), tuple(implies), family, case_sensitive)


def _c(name, category, *patterns, implies=()) -> Term:
    return Term(name, "concept", category, tuple(patterns), tuple(implies))


TECHNOLOGIES: tuple[Term, ...] = (
    # Languages
    _t("Python", "programming_language", r"python(?:\s?3)?"),
    _t("Java", "programming_language", r"java(?!\s*script)", family="jvm_language"),
    _t("Scala", "programming_language", r"scala", family="jvm_language"),
    _t("Kotlin", "programming_language", r"kotlin", family="jvm_language"),
    _t("SQL", "programming_language", r"sql", r"t-sql", r"ansi sql", r"spark sql"),
    _t(
        "Go",
        "programming_language",
        r"golang",
        r"Go(?=\s*(?:,|/|\)|and\b|or\b|programming|language))",
        case_sensitive=True,
    ),
    _t("Rust", "programming_language", r"rust(?=\s*(?:,|/|\)|and\b|or\b|programming|language))"),
    _t("C++", "programming_language", r"c\+\+"),
    _t("C#", "programming_language", r"c#", r"\.net"),
    _t("JavaScript", "programming_language", r"javascript", r"node\.?js", r"typescript"),
    _t("Bash", "programming_language", r"bash", r"shell scripting", r"unix shell"),
    # Clouds
    _t(
        "Azure",
        "cloud",
        r"azure",
        r"microsoft azure",
        implies=("Cloud data platforms",),
        family="cloud_provider",
    ),
    _t(
        "AWS",
        "cloud",
        r"aws",
        r"amazon web services",
        implies=("Cloud data platforms",),
        family="cloud_provider",
    ),
    _t(
        "GCP",
        "cloud",
        r"gcp",
        r"google cloud(?: platform)?",
        implies=("Cloud data platforms",),
        family="cloud_provider",
    ),
    # Azure services
    _t(
        "Azure Data Factory",
        "orchestration",
        r"azure data factory",
        r"adf",
        r"data factory",
        implies=("Azure",),
        family="orchestrator",
    ),
    _t(
        "Azure Data Lake Storage",
        "storage",
        r"azure data lake(?: storage)?(?: gen\s?2)?",
        r"adls(?: gen\s?2)?",
        implies=("Azure",),
        family="object_storage",
    ),
    _t(
        "Azure Synapse",
        "warehouse",
        r"(?:azure )?synapse(?: analytics)?",
        implies=("Azure", "Data warehousing"),
        family="warehouse",
    ),
    _t(
        "Azure Event Hubs",
        "streaming",
        r"(?:azure )?event ?hubs?",
        implies=("Azure",),
        family="streaming_platform",
    ),
    _t(
        "Azure Functions",
        "cloud_service",
        r"azure functions?",
        r"function apps?",
        implies=("Azure",),
        family="serverless",
    ),
    _t(
        "Azure DevOps",
        "devops",
        r"azure devops",
        r"azure pipelines",
        implies=("Azure", "CI/CD"),
        family="ci_cd",
    ),
    _t("Azure Cosmos DB", "database", r"cosmos ?db", implies=("Azure",), family="nosql"),
    _t(
        "Azure Blob Storage",
        "storage",
        r"azure blob(?: storage)?",
        r"blob storage",
        implies=("Azure",),
        family="object_storage",
    ),
    _t("Azure Stream Analytics", "streaming", r"azure stream analytics", implies=("Azure",)),
    _t(
        "Microsoft Fabric",
        "warehouse",
        r"microsoft fabric",
        implies=("Azure", "Data warehousing"),
        family="warehouse",
    ),
    # AWS services
    _t("Amazon S3", "storage", r"s3", r"amazon s3", implies=("AWS",), family="object_storage"),
    _t(
        "Amazon Redshift",
        "warehouse",
        r"redshift",
        implies=("AWS", "Data warehousing"),
        family="warehouse",
    ),
    _t(
        "AWS Glue",
        "processing",
        r"aws glue",
        r"glue (?:jobs?|catalog|crawlers?|etl)",
        implies=("AWS",),
        family="managed_etl",
    ),
    _t(
        "Amazon EMR",
        "processing",
        r"(?:amazon |aws )emr",
        r"(?-i:EMR)",
        implies=("AWS",),
        family="managed_spark",
    ),
    _t(
        "Amazon Kinesis",
        "streaming",
        r"kinesis(?: data streams| firehose)?",
        r"firehose",
        implies=("AWS",),
        family="streaming_platform",
    ),
    _t(
        "AWS Lambda",
        "cloud_service",
        r"aws lambda",
        r"lambdas?",
        implies=("AWS",),
        family="serverless",
    ),
    _t("Amazon DynamoDB", "database", r"dynamo ?db", r"ddb", implies=("AWS",), family="nosql"),
    _t("Amazon Athena", "warehouse", r"athena", implies=("AWS",)),
    _t(
        "Amazon ECS",
        "infrastructure",
        r"ecs",
        r"fargate",
        implies=("AWS",),
        family="container_orchestration",
    ),
    _t(
        "Amazon EKS",
        "infrastructure",
        r"eks",
        implies=("AWS", "Kubernetes"),
        family="container_orchestration",
    ),
    _t("Amazon SQS", "streaming", r"sqs", implies=("AWS",), family="queue"),
    _t("AWS CDK", "infrastructure", r"aws cdk", r"cdk", implies=("AWS",), family="iac"),
    _t("AWS CloudFormation", "infrastructure", r"cloudformation", implies=("AWS",), family="iac"),
    # GCP services
    _t(
        "BigQuery",
        "warehouse",
        r"big ?query",
        implies=("GCP", "Data warehousing"),
        family="warehouse",
    ),
    _t(
        "Google Dataflow",
        "processing",
        r"(?:google |cloud )?dataflow",
        implies=("GCP",),
        family="processing_engine",
    ),
    _t("Google Dataproc", "processing", r"dataproc", implies=("GCP",), family="managed_spark"),
    _t(
        "Google Pub/Sub",
        "streaming",
        r"pub\s?/\s?sub",
        r"pubsub",
        implies=("GCP",),
        family="streaming_platform",
    ),
    _t(
        "Google Cloud Storage",
        "storage",
        r"gcs",
        r"google cloud storage",
        implies=("GCP",),
        family="object_storage",
    ),
    _t("Google Bigtable", "database", r"bigtable", implies=("GCP",), family="nosql"),
    _t(
        "Cloud Composer",
        "orchestration",
        r"cloud composer",
        implies=("GCP", "Airflow"),
        family="orchestrator",
    ),
    _t(
        "GKE",
        "infrastructure",
        r"gke",
        implies=("GCP", "Kubernetes"),
        family="container_orchestration",
    ),
    # Processing engines
    _t(
        "Spark",
        "processing",
        r"(?<!ada/)(?<!ada )(?:apache )?spark",
        r"spark core",
        implies=("Distributed systems",),
        family="processing_engine",
    ),
    _t(
        "PySpark", "processing", r"pyspark", implies=("Spark", "Python"), family="processing_engine"
    ),
    _t(
        "Spark Structured Streaming",
        "streaming",
        r"(?:spark )?structured streaming",
        implies=("Spark", "Streaming / real-time processing"),
    ),
    _t(
        "Databricks",
        "processing",
        r"(?:azure )?databricks",
        implies=("Spark", "Cloud data platforms", "Data platform engineering"),
        family="managed_spark",
    ),
    _t(
        "Flink",
        "processing",
        r"(?:apache )?flink",
        implies=("Streaming / real-time processing", "Distributed systems"),
        family="processing_engine",
    ),
    _t(
        "Apache Beam",
        "processing",
        r"apache beam",
        r"beam(?= (?:pipelines?|sdk))",
        family="processing_engine",
    ),
    _t("Hadoop", "processing", r"hadoop", r"mapreduce", r"hdfs", family="processing_engine"),
    _t("Hive", "warehouse", r"(?:apache )?hive", r"hiveql"),
    _t("Presto/Trino", "warehouse", r"presto(?:db)?", r"trino", family="query_engine"),
    _t("Pandas", "processing", r"pandas"),
    _t("Dask", "processing", r"dask", family="processing_engine"),
    _t("Ray", "processing", r"ray(?= (?:cluster|framework|serve|core))"),
    _t("dbt", "processing", r"dbt(?: core| cloud)?"),
    # Streaming & messaging
    _t(
        "Kafka",
        "streaming",
        r"(?:apache )?kafka",
        r"kafka streams",
        r"ksql(?:db)?",
        implies=("Streaming / real-time processing",),
        family="streaming_platform",
    ),
    _t("Debezium", "streaming", r"debezium", implies=("Change data capture (CDC)",)),
    _t("Pulsar", "streaming", r"(?:apache )?pulsar", family="streaming_platform"),
    _t("RabbitMQ", "streaming", r"rabbit ?mq", family="queue"),
    # Orchestration
    _t(
        "Airflow",
        "orchestration",
        r"(?:apache )?airflow",
        r"mwaa",
        implies=("Workflow orchestration",),
        family="orchestrator",
    ),
    _t("Dagster", "orchestration", r"dagster", family="orchestrator"),
    _t("Prefect", "orchestration", r"prefect", family="orchestrator"),
    _t("Luigi", "orchestration", r"luigi", family="orchestrator"),
    _t("Oozie", "orchestration", r"oozie", family="orchestrator"),
    _t(
        "AWS Step Functions",
        "orchestration",
        r"step functions",
        implies=("AWS",),
        family="orchestrator",
    ),
    # Lakehouse formats
    _t(
        "Delta Lake",
        "table_format",
        r"delta lake",
        r"delta tables?",
        r"delta format",
        implies=("Data lake / lakehouse",),
        family="table_format",
    ),
    _t("Apache Iceberg", "table_format", r"(?:apache )?iceberg", family="table_format"),
    _t("Apache Hudi", "table_format", r"(?:apache )?hudi", family="table_format"),
    _t("Parquet", "file_format", r"parquet", family="file_format"),
    _t("Avro", "file_format", r"avro", family="file_format"),
    _t("ORC", "file_format", r"orc(?= (?:files?|format))", family="file_format"),
    # Warehouses
    _t(
        "Snowflake",
        "warehouse",
        r"snowflake",
        implies=("Data warehousing", "Cloud data platforms"),
        family="warehouse",
    ),
    _t("ClickHouse", "warehouse", r"click ?house", family="olap"),
    _t("Apache Druid", "warehouse", r"(?:apache )?druid", family="olap"),
    _t("Apache Pinot", "warehouse", r"(?:apache )?pinot", family="olap"),
    _t("Teradata", "warehouse", r"teradata", family="warehouse"),
    # Databases & caches
    _t("PostgreSQL", "database", r"postgres(?:ql)?", family="relational_db"),
    _t("MySQL", "database", r"mysql", family="relational_db"),
    _t(
        "SQL Server",
        "database",
        r"(?:microsoft )?sql server",
        r"mssql",
        implies=("SQL",),
        family="relational_db",
    ),
    _t(
        "Oracle Database",
        "database",
        r"oracle (?:database|db)",
        r"pl\s?/\s?sql",
        implies=("SQL",),
        family="relational_db",
    ),
    _t("MongoDB", "database", r"mongo ?db", family="nosql"),
    _t("Cassandra", "database", r"(?:apache )?cassandra", family="nosql"),
    _t("HBase", "database", r"hbase", family="nosql"),
    _t("Redis", "database", r"redis", implies=("Caching",), family="cache"),
    _t("Memcached", "database", r"memcached?", family="cache"),
    _t("Elasticsearch", "database", r"elastic ?search", r"opensearch", family="search_engine"),
    _t("Neo4j", "database", r"neo4j", family="graph_db"),
    # Infrastructure & DevOps
    _t(
        "Kubernetes",
        "infrastructure",
        r"kubernetes",
        r"k8s",
        r"aks",
        family="container_orchestration",
    ),
    _t(
        "Docker",
        "infrastructure",
        r"docker",
        implies=("Containers & orchestration",),
        family="containers",
    ),
    _t("Terraform", "infrastructure", r"terraform", family="iac"),
    _t("Helm", "infrastructure", r"helm(?= (?:charts?|releases?))", r"helm"),
    _t("Ansible", "infrastructure", r"ansible", family="config_management"),
    _t("Linux", "infrastructure", r"linux", r"unix"),
    _t("Git", "devops", r"git(?!hub|lab)", r"github(?! actions)", r"gitlab(?! ci)"),
    _t(
        "CI/CD",
        "devops",
        r"ci\s?/\s?cd",
        r"continuous (?:integration|delivery|deployment)",
        implies=("CI/CD & DevOps",),
        family="ci_cd",
    ),
    _t("Jenkins", "devops", r"jenkins", implies=("CI/CD",), family="ci_cd"),
    _t("GitHub Actions", "devops", r"github actions", implies=("CI/CD",), family="ci_cd"),
    # Observability
    _t(
        "Prometheus",
        "observability",
        r"prometheus",
        implies=("Observability & monitoring",),
        family="observability",
    ),
    _t("Grafana", "observability", r"grafana", family="observability"),
    _t("Datadog", "observability", r"datadog", family="observability"),
    _t("Splunk", "observability", r"splunk", family="observability"),
    _t("OpenTelemetry", "observability", r"open ?telemetry", r"otel", family="observability"),
    # BI & analytics
    _t("Power BI", "bi", r"power ?bi", family="bi"),
    _t("Tableau", "bi", r"tableau", family="bi"),
    _t("Looker", "bi", r"looker", family="bi"),
    _t("Streamlit", "bi", r"streamlit"),
    # ML
    _t("MLflow", "ml", r"ml ?flow"),
    _t("TensorFlow", "ml", r"tensor ?flow", family="ml_framework"),
    _t("PyTorch", "ml", r"py ?torch", family="ml_framework"),
    _t("scikit-learn", "ml", r"scikit-?learn", r"sklearn"),
    _t("Amazon SageMaker", "ml", r"sage ?maker", implies=("AWS",)),
    # APIs & services
    _t("REST APIs", "api", r"rest(?:ful)?(?: apis?| services?)", r"rest api"),
    _t("GraphQL", "api", r"graphql"),
    _t("gRPC", "api", r"grpc"),
    _t("FastAPI", "api", r"fast ?api", implies=("Python",)),
    _t("Spring Boot", "api", r"spring(?: boot)?", implies=("Java",)),
    _t("JSON Schema", "file_format", r"json schema"),
)

CONCEPTS: tuple[Term, ...] = (
    _c(
        "Data pipelines / ETL",
        "data_engineering",
        r"data pipelines?",
        r"etl",
        r"elt",
        r"extract,? transform,? (?:and )?load",
        r"data engineering",
        r"(?:data|etl|elt|ingestion|streaming|batch|processing|cdc|feature) pipelines?",
    ),
    _c("Data ingestion", "data_engineering", r"ingest(?:ion|ing|ed)?"),
    _c(
        "Streaming / real-time processing",
        "data_engineering",
        r"stream(?:ing|s)?(?: processing| data| pipelines?)?",
        r"real[- ]time",
        r"event[- ]driven",
        r"structured streaming",
        r"micro-?batch(?:es)?",
        r"(?:kafka|event|stream) consumers?",
    ),
    _c("Batch processing", "data_engineering", r"batch(?: processing| jobs?| workloads?)?"),
    _c("Change data capture (CDC)", "data_engineering", r"cdc", r"change data capture", r"binlog"),
    _c(
        "Distributed systems",
        "distributed_systems",
        r"distributed systems?",
        r"distributed (?:data )?processing",
        r"distributed computing",
        r"distributed (?:ingestion|storage|architectures?)",
    ),
    _c(
        "Data modeling",
        "data_engineering",
        r"data model(?:l)?ing",
        r"dimensional model(?:l)?ing",
        r"star schemas?",
        r"schema design",
        r"data models?",
    ),
    _c("Data warehousing", "data_engineering", r"data warehous(?:e|es|ing)", r"warehous(?:e|ing)"),
    _c(
        "Data lake / lakehouse", "data_engineering", r"data lakes?", r"lake ?houses?", r"delta lake"
    ),
    _c(
        "Data quality",
        "data_engineering",
        r"data quality",
        r"data validation",
        r"validation frameworks?",
        r"reconciliation",
        r"data completeness",
        r"quality rules",
        r"quarantine",
    ),
    _c(
        "Data governance & compliance",
        "governance",
        r"data governance",
        r"lineage",
        r"data catalog(?:ue)?",
        r"pii",
        r"gdpr",
        r"compliance",
        r"audit(?: records| readiness)?",
    ),
    _c(
        "Performance tuning",
        "performance",
        r"performance tuning",
        r"tun(?:e|ed|ing)",
        r"optimi[sz](?:e|ed|es|ing|ation)",
        r"query (?:performance|optimi[sz]ation)",
        r"(?:query|execution) (?:latency|time)",
        r"improv(?:e|ed|es|ing) (?:\w+ )?(?:performance|latency)",
    ),
    _c(
        "Scalability / high throughput",
        "performance",
        r"scal(?:e|able|ability|ing)",
        r"high[- ]throughput",
        r"throughput",
        r"large[- ]scale",
        r"petabytes?",
        r"terabytes?",
        r"\d+(?:\.\d+)?\s?(?:tb|pb)\+?",
        r"rps",
        r"high[- ]traffic",
        r"high[- ]volume",
    ),
    _c("Low-latency serving", "performance", r"low[- ]latency", r"p99", r"latency"),
    _c(
        "Reliability / fault tolerance",
        "reliability",
        r"reliab(?:le|ility)",
        r"fault[- ]toleran(?:t|ce)",
        r"resilien(?:t|ce|cy)",
        r"high availability",
        r"retr(?:y|ies)",
        r"dead[- ]letter",
        r"backoff",
        r"checkpoint(?:ing|s|-based)?",
        r"idempoten(?:t|cy)",
        r"zero data loss",
        r"rollback",
        r"disaster recovery",
        r"slas?",
    ),
    _c("Caching", "architecture", r"cach(?:e|es|ing)"),
    _c(
        "API development",
        "architecture",
        r"apis?",
        r"rest(?:ful)? apis?",
        r"restful",
        r"endpoints?",
        r"microservices?",
    ),
    _c("Data migration", "data_engineering", r"migrat(?:e|ed|ion|ions|ing)", r"cutover"),
    _c(
        "Partitioning & storage layout",
        "data_engineering",
        r"partition(?:ing|ed|s)?",
        r"bucketing",
        r"storage layouts?",
        r"sharding",
        r"file layouts?",
        r"z-?order(?:ing)?",
    ),
    _c(
        "Workflow orchestration",
        "data_engineering",
        r"orchestrat(?:e|ed|ion|ing)",
        r"workflow scheduling",
        r"dags?",
    ),
    _c(
        "Observability & monitoring",
        "reliability",
        r"observability",
        r"monitoring",
        r"alerting",
        r"metrics",
        r"telemetry",
    ),
    _c("CI/CD & DevOps", "devops", r"ci\s?/\s?cd", r"devops", r"deployment pipelines?"),
    _c(
        "Infrastructure as code",
        "infrastructure",
        r"infrastructure as code",
        r"iac",
        r"terraform",
        r"cloudformation",
    ),
    _c(
        "Containers & orchestration",
        "infrastructure",
        r"containers?",
        r"containeri[sz]ation",
        r"docker",
        r"kubernetes",
    ),
    _c(
        "System design & architecture",
        "architecture",
        r"system design",
        r"architect(?:ure|ures|ed|ing)",
        r"design(?:ed|ing)? (?:and build|scalable|distributed)",
        r"technical design",
        r"design(?:ed)? (?:of|a|an|the)(?= )",
        r"redesign(?:ed|ing)?",
    ),
    _c(
        "Metadata-driven frameworks",
        "data_engineering",
        r"metadata[- ]driven",
        r"declarative",
        r"config(?:uration)?[- ]driven",
    ),
    _c("Schema evolution", "data_engineering", r"schema evolution", r"schema changes"),
    _c(
        "Rate limiting & backpressure",
        "reliability",
        r"rate[- ]limit(?:ing|s|er)?",
        r"backpressure",
        r"throttl(?:e|ed|ing)",
        r"traffic spikes",
    ),
    _c(
        "Concurrency",
        "distributed_systems",
        r"concurren(?:cy|t)",
        r"multi-?thread(?:ed|ing)?",
        r"parallel(?:ism|ization)?",
    ),
    _c(
        "Cloud data platforms",
        "cloud",
        r"cloud(?:-based)? (?:data )?platforms?",
        r"cloud data",
        r"cloud infrastructure",
        r"cloud-native",
    ),
    _c(
        "Data platform engineering",
        "data_engineering",
        r"data platforms?",
        r"data infrastructure",
        r"ingestion platforms?",
        r"platform engineering",
    ),
    _c(
        "SQL & query optimization",
        "data_engineering",
        r"query (?:tuning|optimi[sz]ation)",
        r"full-table scans?",
        r"partition pruning",
        r"indexing",
    ),
    _c(
        "Machine learning & features",
        "ml",
        r"machine learning",
        r"ml(?: models?| pipelines?)?",
        r"feature (?:engineering|stores?|pipelines?)",
        r"mlops",
        r"model (?:training|serving)",
    ),
    _c(
        "Analytics & BI reporting",
        "analytics",
        r"dashboards?",
        r"kpis?",
        r"reporting (?:infrastructure|dashboards?|tools?|layer|solutions?|systems?)",
        r"visuali[sz]ations?",
        r"business intelligence",
        r"(?:business|data[- ]backed) insights",
    ),
    _c(
        "Testing & code quality",
        "engineering_practice",
        r"unit tests?",
        r"testing",
        r"test automation",
        r"code reviews?",
    ),
    _c("Data structures & algorithms", "engineering_practice", r"data structures", r"algorithms"),
    _c(
        "Leadership & mentoring",
        "leadership",
        r"mentor(?:ing|ed|s)?",
        r"lead(?:ing)? (?:\w+ ){0,7}teams?",
        r"people management",
        r"manag(?:e|ing) (?:a )?team",
        r"manag(?:e|ing) engineers",
        r"engineering manager",
        r"tech(?:nical)? lead",
    ),
    _c(
        "Ownership & end-to-end delivery",
        "leadership",
        r"end[- ]to[- ]end",
        r"ownership",
        r"own(?:ed|s)? (?:the )?(?:design|delivery|roadmap)",
        r"drive(?:s|n)?",
        r"drove",
    ),
    _c(
        "Stakeholder collaboration",
        "leadership",
        r"stakeholders?",
        r"cross[- ]functional",
        r"collaborat(?:e|ed|ion|ing)",
    ),
)

ALL_TERMS: tuple[Term, ...] = TECHNOLOGIES + CONCEPTS
TERMS_BY_NAME: dict[str, Term] = {term.name: term for term in ALL_TERMS}

# How much experience with one family member transfers to a requirement for another.
FAMILY_TRANSFER = {
    "cloud_provider": 0.5,
    "streaming_platform": 0.6,
    "orchestrator": 0.6,
    "warehouse": 0.6,
    "table_format": 0.7,
    "processing_engine": 0.5,
    "managed_spark": 0.7,
    "object_storage": 0.7,
    "relational_db": 0.7,
    "nosql": 0.5,
    "cache": 0.7,
    "container_orchestration": 0.5,
    "iac": 0.6,
    "ci_cd": 0.7,
    "observability": 0.6,
    "bi": 0.7,
    "jvm_language": 0.5,
    "serverless": 0.6,
    "queue": 0.6,
    "managed_etl": 0.5,
    "olap": 0.6,
    "file_format": 0.8,
}

# Concepts so generic that they only count as supporting evidence, never as requirements.
WEAK_CONCEPTS = {"Data ingestion", "API development", "Stakeholder collaboration"}


@dataclass(frozen=True)
class TermHit:
    name: str
    kind: str
    category: str
    text: str
    start: int


def find_terms(text: str | None, *, kinds: Iterable[str] = ("tech", "concept")) -> list[TermHit]:
    """All taxonomy terms in ``text``, in order of first appearance."""
    if not text:
        return []
    wanted = set(kinds)
    hits: dict[str, TermHit] = {}
    for term in ALL_TERMS:
        if term.kind not in wanted:
            continue
        match = term.regex.search(text)
        if match and term.name not in hits:
            hits[term.name] = TermHit(
                term.name, term.kind, term.category, match.group(0), match.start()
            )
    _drop_shadowed(hits, text)
    return sorted(hits.values(), key=lambda hit: hit.start)


def _drop_shadowed(hits: dict[str, TermHit], text: str) -> None:
    """Remove matches that are only part of a longer, more specific term at the same place
    (``Azure`` inside ``Azure Data Factory`` still counts via ``implies``)."""
    lower = text.lower()
    if "SQL" in hits and not re.search(
        r"(?<![A-Za-z/])(?<!my)(?<!no)(?<!postgre)sql(?! server)", lower
    ):
        hits.pop("SQL", None)
    if "Git" in hits and not re.search(r"(?<![A-Za-z])git(?![A-Za-z])", lower):
        hits.pop("Git", None)


def term_names(text: str | None, kind: str | None = None) -> list[str]:
    kinds = (kind,) if kind else ("tech", "concept")
    return [hit.name for hit in find_terms(text, kinds=kinds)]


def expand_implied(names: Iterable[str]) -> set[str]:
    """Close a set of term names under ``implies``."""
    result: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in result:
            continue
        result.add(name)
        term = TERMS_BY_NAME.get(name)
        if term:
            pending.extend(term.implies)
    return result


def family_peers(name: str) -> list[str]:
    term = TERMS_BY_NAME.get(name)
    if term is None or term.family is None:
        return []
    return [
        other.name for other in TECHNOLOGIES if other.family == term.family and other.name != name
    ]


def kind_of(name: str) -> str:
    term = TERMS_BY_NAME.get(name)
    return term.kind if term else "tech"


def category_of(name: str) -> str:
    term = TERMS_BY_NAME.get(name)
    return term.category if term else "other"


# Categories reported in JD analysis.
CATEGORY_GROUPS = {
    "programming_languages": {"programming_language"},
    "cloud": {"cloud", "cloud_service"},
    "data_technologies": {
        "processing",
        "streaming",
        "orchestration",
        "storage",
        "warehouse",
        "table_format",
        "file_format",
        "database",
    },
    "infrastructure": {"infrastructure", "devops", "observability"},
    "distributed_systems": {"distributed_systems", "reliability", "performance"},
    "architecture": {"architecture"},
    "ml": {"ml"},
    "analytics": {"bi", "analytics"},
}


def group_terms(names: Iterable[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {group: [] for group in CATEGORY_GROUPS}
    for name in names:
        category = category_of(name)
        for group, categories in CATEGORY_GROUPS.items():
            if category in categories and name not in grouped[group]:
                grouped[group].append(name)
    return {group: values for group, values in grouped.items() if values}


DOMAINS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        (
            "Payments & financial services",
            r"\b(payments?|fintech|banking|bank|credit|lending|loans?|financial services|"
            r"trading|card (?:issuing|network)|kyc|aml|fraud|wealth|insurance|capital markets)\b",
        ),
        (
            "E-commerce & retail",
            r"\b(e-?commerce|commerce|retail|marketplace|pricing|catalog(?:ue)?|shopping|"
            r"checkout|merchants?)\b",
        ),
        ("Advertising & marketing", r"\b(advertising|ad ?tech|ads|campaigns?|marketing)\b"),
        (
            "Healthcare & life sciences",
            r"\b(healthcare|health|clinical|medical|pharma|patients?)\b",
        ),
        ("Telecommunications", r"\b(telecom(?:munications)?|5g|network operators?)\b"),
        (
            "Logistics & supply chain",
            r"\b(logistics|supply chain|fulfil?ment centers?|last[- ]mile|"
            r"warehouse operations)\b",
        ),
        ("Travel & mobility", r"\b(travel|hospitality|ride[- ]?sharing|mobility|airlines?)\b"),
        ("Media & entertainment", r"\b(media|entertainment|streaming video|gaming|games?)\b"),
        ("AI & machine learning", r"\b(ai|artificial intelligence|machine learning|llms?|genai)\b"),
        ("Cloud & developer platforms", r"\b(cloud platform|saas|developer tools|devtools|paas)\b"),
        ("Cybersecurity", r"\b(security operations|cybersecurity|threat|siem)\b"),
        ("Semiconductors & hardware", r"\b(semiconductors?|gpus?|chips?|hardware)\b"),
    )
)


def find_domains(text: str | None, *, emphasis: str | None = None, minimum: int = 1) -> list[str]:
    """Business domains mentioned at least ``minimum`` times in ``text`` (or once in
    ``emphasis``, such as a job title or company description)."""
    if not text and not emphasis:
        return []
    found: list[str] = []
    for name, pattern in DOMAINS:
        count = len(pattern.findall(text or ""))
        if (count >= minimum or (emphasis and pattern.search(emphasis))) and name not in found:
            found.append(name)
    return found


_METRIC = re.compile(
    r"(?<![A-Za-z0-9])(?:[$\u20b9\u00a3\u20ac])?\d[\d,]*(?:\.\d+)?\s?"
    r"(?:%|x\b|ms\b|mins?\b|minutes?\b|hrs?\b|hours?\b|days?\b|rps\b|qps\b|tb\+?|"
    r"gb(?:/day)?\+?|pb\+?|k\+?|m\+?|b\+?|s\b|\+)?",
    re.I,
)


def find_metrics(text: str | None) -> list[str]:
    """Numbers with units ("42%", "4TB+", "80ms", "20K+"). Years in dates are excluded."""
    if not text:
        return []
    metrics: list[str] = []
    for match in _METRIC.finditer(text):
        value = match.group(0).strip()
        digits = re.sub(r"[^\d]", "", value)
        if not digits:
            continue
        if re.fullmatch(r"(?:19|20)\d\d", value):
            continue
        if value not in metrics:
            metrics.append(value)
    return metrics


def normalize_metric(value: str) -> str:
    """Canonical form for comparing metrics: lowercase, no spaces/commas, "+" dropped."""
    return re.sub(r"[\s,+]", "", value.lower()).replace("hours", "hrs").replace("hour", "hr")
