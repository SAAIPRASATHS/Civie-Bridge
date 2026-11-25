"""Synthetic dataset generation, seeding and validation reporting (Phase 2).

Nothing in this package calls an LLM: the dataset is template-driven and
deterministic, and the department directory plus service guidelines are
controlled reference data so no agent has to invent either.
"""

from app.datasets.generator import (
    DEFAULT_COUNT,
    DEFAULT_DUPLICATE_COUNT,
    DEFAULT_SEED,
    DistributionSummary,
    SyntheticComplaint,
    generate_complaints,
    summarise,
    write_dataset_files,
)
from app.datasets.reference_data import (
    DEPARTMENT_DIRECTORY,
    DEPARTMENT_PROVENANCE,
    GUIDELINE_PROVENANCE,
    SERVICE_GUIDELINES,
)
from app.datasets.report import (
    DatasetReport,
    ValidationCheck,
    build_report,
    render_markdown,
    write_report_files,
)
from app.datasets.seed import (
    SeedReport,
    database_revision,
    reset_all_data,
    seed_database,
    seed_reference_data,
    table_row_counts,
)

__all__ = [
    "DEFAULT_COUNT",
    "DEFAULT_DUPLICATE_COUNT",
    "DEFAULT_SEED",
    "DEPARTMENT_DIRECTORY",
    "DEPARTMENT_PROVENANCE",
    "GUIDELINE_PROVENANCE",
    "SERVICE_GUIDELINES",
    "DatasetReport",
    "DistributionSummary",
    "SeedReport",
    "SyntheticComplaint",
    "ValidationCheck",
    "build_report",
    "database_revision",
    "generate_complaints",
    "render_markdown",
    "reset_all_data",
    "seed_database",
    "seed_reference_data",
    "summarise",
    "table_row_counts",
    "write_dataset_files",
    "write_report_files",
]
