"""Deterministic synthetic complaint generator.

The generator never calls an LLM. It fills a fixed set of templates with
synthetic municipal locations so the same seed always produces byte-identical
data - which is what makes the Phase 2 distribution tests meaningful.

Three things are deliberately planted for later phases to detect:

* **Duplicates** - a configurable number of near-copies of earlier complaints,
  each carrying ``duplicate_of_reference_id`` so the duplicate-detection agent
  has ground truth to score against.
* **Missing coordinates** - a small share of complaints have no location fix,
  which must remain valid (both coordinates NULL).
* **Language spread** - every complaint is written in English, Tamil, or
  Tamil written in Latin script (transliterated).
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.datasets.reference_data import SERVICE_GUIDELINES
from app.models.enums import (
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
    ComplaintStatus,
)

#: Fixed anchor so generated timestamps are reproducible for a given seed.
ANCHOR_DATETIME = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)

#: Default seed. Changing it changes the data, never the schema or the tests'
#: ability to validate the invariants.
DEFAULT_SEED = 20261125
DEFAULT_COUNT = 1000
DEFAULT_DUPLICATE_COUNT = 50

#: Synthetic municipal localities. Coordinates sit inside a plausible urban
#: bounding box; none of these are real administrative boundaries.
LOCALITIES: tuple[dict[str, Any], ...] = (
    {"name": "Anna Nagar (synthetic)", "ward": "Ward 10", "lat": 13.0850, "lon": 80.2100},
    {"name": "Besant Nagar (synthetic)", "ward": "Ward 26", "lat": 13.0000, "lon": 80.2700},
    {"name": "T Nagar (synthetic)", "ward": "Ward 13", "lat": 13.0400, "lon": 80.2330},
    {"name": "Adyar (synthetic)", "ward": "Ward 30", "lat": 13.0060, "lon": 80.2570},
    {"name": "Velachery (synthetic)", "ward": "Ward 32", "lat": 12.9790, "lon": 80.2210},
    {"name": "Porur (synthetic)", "ward": "Ward 41", "lat": 13.0350, "lon": 80.1580},
    {"name": "Ambattur (synthetic)", "ward": "Ward 48", "lat": 13.0980, "lon": 80.1610},
    {"name": "Perungudi (synthetic)", "ward": "Ward 37", "lat": 12.9700, "lon": 80.2450},
    {"name": "Kodambakkam (synthetic)", "ward": "Ward 14", "lat": 13.0500, "lon": 80.2240},
    {"name": "Guindy (synthetic)", "ward": "Ward 28", "lat": 13.0070, "lon": 80.2200},
    {"name": "Vadapalani (synthetic)", "ward": "Ward 12", "lat": 13.0500, "lon": 80.2120},
    {"name": "Sholinganallur (synthetic)", "ward": "Ward 40", "lat": 12.9000, "lon": 80.2270},
)

#: Weighted category mix. Waste and water complaints dominate real grievance
#: queues, so the synthetic data reflects that skew.
CATEGORY_WEIGHTS: dict[ComplaintCategory, float] = {
    ComplaintCategory.WASTE_MANAGEMENT: 0.30,
    ComplaintCategory.ROAD_MAINTENANCE: 0.25,
    ComplaintCategory.WATER_SUPPLY: 0.25,
    ComplaintCategory.STREET_LIGHTING: 0.20,
}

LANGUAGE_WEIGHTS: dict[ComplaintLanguage, float] = {
    ComplaintLanguage.EN: 0.60,
    ComplaintLanguage.TA: 0.25,
    ComplaintLanguage.TA_LATN: 0.15,
}

PRIORITY_WEIGHTS: dict[ComplaintPriority, float] = {
    ComplaintPriority.LOW: 0.25,
    ComplaintPriority.MEDIUM: 0.40,
    ComplaintPriority.HIGH: 0.25,
    ComplaintPriority.CRITICAL: 0.10,
}

STATUS_WEIGHTS: dict[ComplaintStatus, float] = {
    ComplaintStatus.SUBMITTED: 0.24,
    ComplaintStatus.UNDER_REVIEW: 0.18,
    ComplaintStatus.ASSIGNED: 0.15,
    ComplaintStatus.IN_PROGRESS: 0.13,
    ComplaintStatus.RESOLVED: 0.11,
    ComplaintStatus.CLOSED: 0.05,
    ComplaintStatus.AWAITING_CLARIFICATION: 0.05,
    ComplaintStatus.ESCALATED: 0.05,
    ComplaintStatus.REJECTED: 0.04,
}

#: ``(title, description)`` templates keyed by category then language.
TEMPLATES: dict[ComplaintCategory, dict[ComplaintLanguage, tuple[tuple[str, str], ...]]] = {
    ComplaintCategory.WASTE_MANAGEMENT: {
        ComplaintLanguage.EN: (
            (
                "Garbage not collected for five days",
                "Garbage has not been collected at {location} for the past five days and the smell is unbearable.",
            ),
            (
                "Overflowing waste bin",
                "The waste bin near {location} is overflowing and has not been cleared for over a week.",
            ),
            (
                "Street not swept",
                "The street at {location} has not been swept and litter is piling up near the shops.",
            ),
        ),
        ComplaintLanguage.TA: (
            (
                "குப்பை அள்ளப்படவில்லை",
                "{location} பகுதியில் கடந்த ஐந்து நாட்களாக குப்பை அள்ளப்படவில்லை. துர்நாற்றம் பரவுகிறது.",
            ),
            (
                "குப்பைத் தொட்டி நிரம்பி வழிகிறது",
                "{location} அருகில் உள்ள குப்பைத் தொட்டி நிரம்பி வழிகிறது, ஒரு வாரமாக சுத்தம் செய்யப்படவில்லை.",
            ),
            (
                "தெரு சுத்தம் செய்யப்படவில்லை",
                "{location} தெருவில் துப்புரவு பணி நடைபெறவில்லை, குப்பை குவிந்துள்ளது.",
            ),
        ),
        ComplaintLanguage.TA_LATN: (
            (
                "Kuppai allapadavillai",
                "{location} paguthiyil kadandha aindhu naatkalaga kuppai allapadavillai. Dhurnaatram paravugiradhu.",
            ),
            (
                "Kuppai thotti nirambi vazhigiradhu",
                "{location} arugil ulla kuppai thotti nirambi vazhigiradhu, oru vaaramaga suththam seiyapadavillai.",
            ),
            (
                "Theru suththam seiyapadavillai",
                "{location} theruvil thuppuravu pani nadakkapadavillai, kuppai kuvindhullathu.",
            ),
        ),
    },
    ComplaintCategory.ROAD_MAINTENANCE: {
        ComplaintLanguage.EN: (
            (
                "Large pothole on the road",
                "A large pothole has formed on the road at {location} and two-wheelers keep skidding.",
            ),
            (
                "Damaged road surface",
                "The road surface at {location} is badly damaged with loose gravel and deep ruts.",
            ),
            (
                "Road repair work left incomplete",
                "Road laying work at {location} was left incomplete and several open pits remain.",
            ),
        ),
        ComplaintLanguage.TA: (
            (
                "சாலையில் பெரிய பள்ளம்",
                "{location} சாலையில் பெரிய பள்ளம் உள்ளது. இருசக்கர வாகனங்கள் வழுக்கி விபத்தில் சிக்குகின்றன.",
            ),
            (
                "சாலை சேதமடைந்துள்ளது",
                "{location} சாலை முழுவதும் சேதமடைந்துள்ளது, பயணம் செய்வது மிகவும் கடினம்.",
            ),
            (
                "சாலை பழுது நிலவரை முடிக்கப்படவில்லை",
                "{location} பகுதியில் சாலை அமைக்கும் பணி முடிக்கப்படவில்லை, திறந்த குழிகள் உள்ளன.",
            ),
        ),
        ComplaintLanguage.TA_LATN: (
            (
                "Saalaiyil periya pallam",
                "{location} saalaiyil periya pallam ullathu. Iruzhakka vahanangal vazhukki accident-il sikkugindrana.",
            ),
            (
                "Saalai kedamadaindhullathu",
                "{location} saalai muzhuvadhum kedamadaindhullathu, payanam seivadhu kashtam.",
            ),
            (
                "Saalai pazhudhu pani mudiya villai",
                "{location} paguthiyil saalai amaippu pani mudiya villai, thirandha kuzhigal ullana.",
            ),
        ),
    },
    ComplaintCategory.WATER_SUPPLY: {
        ComplaintLanguage.EN: (
            (
                "No drinking water supply",
                "Drinking water supply has been stopped at {location} for the last three days.",
            ),
            (
                "Water pipeline leak",
                "A drinking water pipeline is leaking near {location} and clean water is being wasted.",
            ),
            (
                "Muddy water from the tap",
                "Muddy water is coming through the tap at {location} and residents are afraid to drink it.",
            ),
        ),
        ComplaintLanguage.TA: (
            ("குடிநீர் விநியோகம் இல்லை", "{location} பகுதியில் கடந்த மூன்று நாட்களாக குடிநீர் விநியோகம் இல்லை."),
            ("குடிநீர் குழாய் கசிவு", "{location} அருகே குடிநீர் குழாய் உடைந்து சுத்தமான தண்ணீர் வீணாகிறது."),
            (
                "குழாயில் கலங்கலான தண்ணீர்",
                "{location} பகுதியில் குழாயில் கலங்கலான தண்ணீர் வருகிறது, குடிக்க பயமாக உள்ளது.",
            ),
        ),
        ComplaintLanguage.TA_LATN: (
            (
                "Kudineer viniyogam illai",
                "{location} paguthiyil kadandha moonru naatkalaga kudineer viniyogam illai.",
            ),
            (
                "Kudineer kuzhaai kasivu",
                "{location} arugae kudineer kuzhaai udaindhu suthamaana thanneer veenagirdhu.",
            ),
            (
                "Kuzhaaiyil kalangalana thanneer",
                "{location} paguthiyil kuzhaaiyil kalangalana thanneer varugirdhu, kudikka payamaaga ullathu.",
            ),
        ),
    },
    ComplaintCategory.STREET_LIGHTING: {
        ComplaintLanguage.EN: (
            (
                "Street lights not working",
                "The street lights at {location} have not been working and the road is completely dark at night.",
            ),
            (
                "Damaged street light pole",
                "A street light pole near {location} is damaged and the light has been off for several days.",
            ),
            (
                "Dark stretch needs lighting",
                "The stretch at {location} has no working lights, making it unsafe for pedestrians after dark.",
            ),
        ),
        ComplaintLanguage.TA: (
            (
                "தெரு விளக்கு வேலை செய்யவில்லை",
                "{location} தெருவில் உள்ள தெரு விளக்குகள் வேலை செய்யவில்லை, இரவில் இருட்டாக உள்ளது.",
            ),
            (
                "தெரு விளக்கு கம்பம் சேதம்",
                "{location} அருகில் உள்ள தெரு விளக்கு கம்பம் சேதமடைந்துள்ளது, விளக்கு பல நாட்களாக ஒளிரவில்லை.",
            ),
            (
                "இருண்ட தெரு",
                "{location} தெரு முழுவதும் இருட்டாக உள்ளது, நடந்து செல்பவர்களுக்கு பாதுகாப்பு இல்லை.",
            ),
        ),
        ComplaintLanguage.TA_LATN: (
            (
                "Theru vilakku velai seiyavillai",
                "{location} theruvil ulla theru vilakkugal velai seiyavillai, iravil iruttaga ullathu.",
            ),
            (
                "Theru vilakku kambam kedam",
                "{location} arugil ulla theru vilakku kambam kedamadaindhullathu, vilakku pala naatkalaga oliravillai.",
            ),
            (
                "Irunda theru",
                "{location} theru muzhuvadhum iruttaga ullathu, nadandhu selvargalukku paadhukappu illai.",
            ),
        ),
    },
}

#: Appended for ``high`` and ``critical`` complaints so the priority band is
#: visible in the free text as well as the stored enum.
SEVERITY_SUFFIX: dict[ComplaintLanguage, dict[ComplaintPriority, str]] = {
    ComplaintLanguage.EN: {
        ComplaintPriority.HIGH: " This is causing a serious safety risk for residents.",
        ComplaintPriority.CRITICAL: " This is an emergency and needs immediate attention.",
    },
    ComplaintLanguage.TA: {
        ComplaintPriority.HIGH: " இது மக்களுக்கு பெரும் ஆபத்தை ஏற்படுத்துகிறது.",
        ComplaintPriority.CRITICAL: " இது அவசர நிலை, உடனடியாக கவனிக்க வேண்டும்.",
    },
    ComplaintLanguage.TA_LATN: {
        ComplaintPriority.HIGH: " Idhu makkalukku perum aapaththai erpaduthugiradhu.",
        ComplaintPriority.CRITICAL: " Idhu avasara nilai, udanadiyaga kavanikka vendum.",
    },
}

#: Share of complaints that arrive without a location fix.
MISSING_COORDINATE_RATE = 0.06

#: SLA duration per category, taken from the prototype guidelines.
SLA_HOURS_BY_CATEGORY: dict[ComplaintCategory, int] = {
    guide["category"]: guide["sla_hours"] for guide in SERVICE_GUIDELINES
}


@dataclass(slots=True)
class SyntheticComplaint:
    """A single generated complaint, shaped like the ``complaints`` table."""

    reference_id: str
    description: str
    title: str
    language: str
    category: str
    priority: str
    status: str
    location_text: str | None
    ward: str | None
    latitude: float | None
    longitude: float | None
    location_accuracy_m: int | None
    reported_at: datetime
    submitted_at: datetime
    sla_due_at: datetime
    sla_breached: bool
    resolved_at: datetime | None
    closed_at: datetime | None
    needs_clarification: bool
    clarification_question: str | None
    duplicate_of_reference_id: str | None
    duplicate_similarity: float | None
    is_synthetic: bool = True

    def as_record(self) -> dict[str, Any]:
        """Return a JSON/CSV-friendly record (datetimes as ISO-8601 strings)."""
        record = asdict(self)
        for key in ("reported_at", "submitted_at", "sla_due_at", "resolved_at", "closed_at"):
            value = record[key]
            record[key] = value.isoformat() if isinstance(value, datetime) else None
        return record


def _allocate(total: int, weights: dict[Any, float]) -> dict[Any, int]:
    """Split ``total`` across ``weights`` using the largest-remainder method.

    Deterministic, so the exact category / language / priority / status counts
    never drift with the seed.
    """
    keys = list(weights)
    exact = {key: total * weights[key] for key in keys}
    counts = {key: int(value) for key, value in exact.items()}
    remainder = total - sum(counts.values())
    for key in sorted(keys, key=lambda k: exact[k] - int(exact[k]), reverse=True)[:remainder]:
        counts[key] += 1
    return counts


def _weighted_pool(total: int, weights: dict[Any, float], rng: random.Random) -> list[Any]:
    """Return a shuffled list containing ``weights``-proportional members."""
    pool: list[Any] = []
    for key, count in _allocate(total, weights).items():
        pool.extend([key] * count)
    rng.shuffle(pool)
    return pool


def _reference_id(index: int, anchor: datetime) -> str:
    return f"CB-{anchor.year}-{index:06d}"


def generate_complaints(
    count: int = DEFAULT_COUNT,
    duplicate_count: int = DEFAULT_DUPLICATE_COUNT,
    seed: int = DEFAULT_SEED,
) -> list[SyntheticComplaint]:
    """Generate ``count`` primary complaints plus ``duplicate_count`` planted copies."""
    if count <= 0:
        raise ValueError("count must be positive")
    if duplicate_count < 0:
        raise ValueError("duplicate_count must not be negative")

    rng = random.Random(seed)

    categories = _weighted_pool(count, CATEGORY_WEIGHTS, rng)
    languages = _weighted_pool(count, LANGUAGE_WEIGHTS, rng)
    priorities = _weighted_pool(count, PRIORITY_WEIGHTS, rng)
    statuses = _weighted_pool(count, STATUS_WEIGHTS, rng)

    complaints: list[SyntheticComplaint] = []
    for index in range(count):
        category: ComplaintCategory = categories[index]
        language: ComplaintLanguage = languages[index]
        priority: ComplaintPriority = priorities[index]
        status: ComplaintStatus = statuses[index]

        complaints.append(
            _build_complaint(
                index=index + 1,
                category=category,
                language=language,
                priority=priority,
                status=status,
                rng=rng,
                anchor=ANCHOR_DATETIME,
            )
        )

    # Plant duplicates by copying existing complaints. The originals are spread
    # across the whole range so duplicate detection cannot rely on proximity.
    if duplicate_count:
        originals = rng.sample(complaints, k=min(duplicate_count, len(complaints)))
        for offset, original in enumerate(originals, start=count + 1):
            complaints.append(
                _build_duplicate(
                    index=offset,
                    original=original,
                    rng=rng,
                    anchor=ANCHOR_DATETIME,
                )
            )

    return complaints


def _build_complaint(
    *,
    index: int,
    category: ComplaintCategory,
    language: ComplaintLanguage,
    priority: ComplaintPriority,
    status: ComplaintStatus,
    rng: random.Random,
    anchor: datetime,
) -> SyntheticComplaint:
    locality = rng.choice(LOCALITIES)
    template_title, template_body = rng.choice(TEMPLATES[category][language])

    title = template_title
    description = template_body.format(location=locality["name"])
    if priority in SEVERITY_SUFFIX[language]:
        description += SEVERITY_SUFFIX[language][priority]

    has_coordinates = rng.random() >= MISSING_COORDINATE_RATE
    if has_coordinates:
        latitude: float | None = round(locality["lat"] + rng.uniform(-0.008, 0.008), 6)
        longitude: float | None = round(locality["lon"] + rng.uniform(-0.008, 0.008), 6)
        location_accuracy_m: int | None = rng.choice([10, 15, 20, 25, 30, 50])
    else:
        latitude = longitude = location_accuracy_m = None

    submitted_at = anchor - timedelta(
        days=rng.randint(0, 180), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
    )
    sla_due_at = submitted_at + timedelta(hours=SLA_HOURS_BY_CATEGORY[category])

    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    if status in {ComplaintStatus.RESOLVED, ComplaintStatus.CLOSED}:
        # Resolve some time after submission, either inside or past the SLA.
        resolved_at = submitted_at + timedelta(
            hours=rng.randint(6, SLA_HOURS_BY_CATEGORY[category] * 2)
        )
        if status == ComplaintStatus.CLOSED:
            closed_at = resolved_at + timedelta(hours=rng.randint(2, 48))

    reference_end = closed_at or resolved_at or anchor
    sla_breached = reference_end > sla_due_at

    needs_clarification = status == ComplaintStatus.AWAITING_CLARIFICATION
    clarification_question = _CLARIFICATION_QUESTIONS[language] if needs_clarification else None

    return SyntheticComplaint(
        reference_id=_reference_id(index, anchor),
        description=description,
        title=title,
        language=language.value,
        category=category.value,
        priority=priority.value,
        status=status.value,
        location_text=locality["name"],
        ward=locality["ward"],
        latitude=latitude,
        longitude=longitude,
        location_accuracy_m=location_accuracy_m,
        reported_at=submitted_at,
        submitted_at=submitted_at,
        sla_due_at=sla_due_at,
        sla_breached=sla_breached,
        resolved_at=resolved_at,
        closed_at=closed_at,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        duplicate_of_reference_id=None,
        duplicate_similarity=None,
    )


_CLARIFICATION_QUESTIONS: dict[ComplaintLanguage, str] = {
    ComplaintLanguage.EN: (
        "Could you confirm the nearest landmark and how many days the issue has persisted?"
    ),
    ComplaintLanguage.TA: "அருகில் உள்ள அடையாளத்தையும், பிரச்சினை எத்தனை நாட்களாக உள்ளது என்பதையும் தெரிவிக்க முடியுமா?",
    ComplaintLanguage.TA_LATN: "Arugil ulla adaiyaalathaiyum, prachanai ethanai naatkalaga ulladhu endrum therivikka mudiyuma?",
}


def _build_duplicate(
    *,
    index: int,
    original: SyntheticComplaint,
    rng: random.Random,
    anchor: datetime,
) -> SyntheticComplaint:
    """Copy ``original`` into a planted duplicate with a fresh identity."""
    re_reported = {
        ComplaintLanguage.EN: " (reported again by another resident)",
        ComplaintLanguage.TA: " (மற்றொரு குடிமகனால் மீண்டும் புகார் அளிக்கப்பட்டது)",
        ComplaintLanguage.TA_LATN: " (matroru kudimaganalaal meendum pugaar alikkappattadhu)",
    }[ComplaintLanguage(original.language)]

    submitted_at = min(anchor, original.submitted_at + timedelta(hours=rng.randint(1, 48)))
    return SyntheticComplaint(
        reference_id=_reference_id(index, anchor),
        description=original.description + re_reported,
        title=original.title,
        language=original.language,
        category=original.category,
        priority=original.priority,
        status=ComplaintStatus.DUPLICATE.value,
        location_text=original.location_text,
        ward=original.ward,
        latitude=original.latitude,
        longitude=original.longitude,
        location_accuracy_m=original.location_accuracy_m,
        reported_at=submitted_at,
        submitted_at=submitted_at,
        sla_due_at=original.sla_due_at,
        sla_breached=False,
        resolved_at=None,
        closed_at=None,
        needs_clarification=False,
        clarification_question=None,
        duplicate_of_reference_id=original.reference_id,
        duplicate_similarity=round(rng.uniform(0.82, 0.99), 3),
    )


#: Columns written to the CSV mirror of the dataset.
CSV_FIELDS: tuple[str, ...] = (
    "reference_id",
    "title",
    "description",
    "language",
    "category",
    "priority",
    "status",
    "location_text",
    "ward",
    "latitude",
    "longitude",
    "location_accuracy_m",
    "reported_at",
    "submitted_at",
    "sla_due_at",
    "sla_breached",
    "resolved_at",
    "closed_at",
    "needs_clarification",
    "clarification_question",
    "duplicate_of_reference_id",
    "duplicate_similarity",
    "is_synthetic",
)


def write_dataset_files(complaints: list[SyntheticComplaint], output_dir: Path) -> dict[str, Path]:
    """Write the dataset as CSV and JSON, returning the paths that were written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    records = [complaint.as_record() for complaint in complaints]

    csv_path = output_dir / "complaints_synthetic.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        writer.writerows(records)

    json_path = output_dir / "complaints_synthetic.json"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"csv": csv_path, "json": json_path}


@dataclass(slots=True)
class DistributionSummary:
    """Counts used by the dataset report and its tests."""

    total: int
    primary: int
    duplicates: int
    missing_coordinates: int
    with_coordinates: int
    by_category: dict[str, int] = field(default_factory=dict)
    by_language: dict[str, int] = field(default_factory=dict)
    by_priority: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)


def summarise(complaints: list[SyntheticComplaint]) -> DistributionSummary:
    """Compute the distribution summary for a generated dataset."""
    summary = DistributionSummary(
        total=len(complaints),
        primary=sum(1 for c in complaints if c.duplicate_of_reference_id is None),
        duplicates=sum(1 for c in complaints if c.duplicate_of_reference_id is not None),
        missing_coordinates=sum(1 for c in complaints if c.latitude is None),
        with_coordinates=sum(1 for c in complaints if c.latitude is not None),
    )
    for complaint in complaints:
        summary.by_category[complaint.category] = summary.by_category.get(complaint.category, 0) + 1
        summary.by_language[complaint.language] = summary.by_language.get(complaint.language, 0) + 1
        summary.by_priority[complaint.priority] = summary.by_priority.get(complaint.priority, 0) + 1
        summary.by_status[complaint.status] = summary.by_status.get(complaint.status, 0) + 1
    return summary
