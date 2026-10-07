"""Synthetic resume and job descriptions for Phase 3 tests (no real personal data)."""

from __future__ import annotations

RESUME_TEXT = """Jane Doe
jane.doe@example.com | linkedin.com/in/janedoe | github.com/janedoe | +91-9000000000

Professional Summary
Data engineer focused on streaming platforms and reliable ingestion at scale. Built production pipelines on Azure with Spark and Kafka, leading migrations end-to-end.

Professional Experience
\u2022 Acme Data Services
Senior Analyst (Senior Data Engineer)
Mumbai, India
Mar 2023 - Present
\u25e6 Streaming Ingestion: Built Kafka-based streaming ingestion with exponential backoff for event-driven consumers, cutting consumer lag by 35% across 12 topics.
\u25e6 Batch Optimization: Tuned Spark jobs for 50M+ record workloads, reducing runtime from 4hrs to 1.5hrs.
\u25e6 Reporting Support: Prepared monthly Power BI dashboards for finance stakeholders.
\u25e6 Tech Stack: Python, PySpark, Kafka, SQL, Azure, Databricks

\u2022 Legacy Systems Inc
Associate Engineer (Data Engineer)
Pune, India
Jan 2016 - Dec 2019
\u25e6 Hadoop Pipelines: Maintained Hadoop MapReduce pipelines for 2TB+ daily logs.
\u25e6 Tech Stack: Hadoop, Hive, SQL

Technical Skills
\u2022 Languages: Python, SQL, Java
\u2022 Data: Apache Spark, Apache Kafka, Airflow, Databricks
\u2022 Cloud: Azure (Event Hubs, Synapse)

Relevant Projects
\u2022 LakeFlow \u2014 Lakehouse Orchestration Demo
\u25e6 Platform: Built an Airflow-orchestrated pipeline loading Delta Lake tables with data quality checks.
\u25e6 Stack: Airflow, Delta Lake, Python

Education & Certifications
\u2022 University of Pune
Bachelor of Engineering in Computer Engineering
2015
\u2022 Microsoft Certified: Azure Data Engineer Associate (DP-203)
"""

STREAMING_JD = """About the team
We build the real-time data platform behind our payments products.

What you'll do
- Design and operate streaming data pipelines on Kafka and Spark Structured Streaming
- Improve reliability, backpressure handling and observability of event-driven consumers

Basic qualifications
- 4+ years of experience as a data engineer
- Strong experience with Kafka and Spark
- Experience with AWS technologies such as Redshift, S3 or Glue
- Proficiency in Python and SQL

Preferred qualifications
- Kubernetes
- Apache Iceberg
- Experience with Airflow
"""

SENIOR_JD = """Basic qualifications
- 10+ years of experience building distributed data systems
- Experience leading teams of engineers
- Expertise with Spark and Kafka

Preferred qualifications
- Experience with Flink
"""
