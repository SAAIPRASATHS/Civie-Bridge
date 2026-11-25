"""Controlled prototype reference data: the department directory and service guidelines.

Two ideas matter here:

* The routing agent must never *invent* a department. It may only choose from
  :data:`DEPARTMENT_DIRECTORY`, so every routing decision is traceable to a row
  a human put in the database.
* The resolution agent must never *invent* a service rule. It may only quote
  :data:`SERVICE_GUIDELINES`, whose SLA durations are configurable placeholders.

Every record is explicitly labelled as prototype data. Nothing here is a real
government assignment or an officially published service standard, and the
reporting layer repeats that disclaimer wherever these rows are surfaced.
"""

from __future__ import annotations

from typing import Any

from app.models.enums import ComplaintCategory

#: Shown in the dataset report and the API responses for the department directory.
DEPARTMENT_PROVENANCE = (
    "Prototype directory authored for CivicBridge AI. Illustrative municipal units "
    "only - not a verified real government assignment."
)

#: Shown wherever a service guideline or SLA duration is quoted.
GUIDELINE_PROVENANCE = (
    "Prototype assumption for CivicBridge AI. SLA durations are configurable "
    "placeholders - not an officially published service rule."
)

#: One prototype department per supported complaint category.
DEPARTMENT_DIRECTORY: tuple[dict[str, Any], ...] = (
    {
        "code": "SWM",
        "name": "Municipal Solid Waste Management Department",
        "category": ComplaintCategory.WASTE_MANAGEMENT,
        "description": (
            "Prototype unit responsible for household waste collection, bin "
            "clearance, drain desilting and public toilet upkeep."
        ),
        "contact_email": "waste.management@civicbridge.prototype",
        "contact_phone": "+91-44-0000-1001",
        "office_address": "Prototype Civic Centre, Zone 1, Synthetic City",
        "jurisdiction": "Prototype municipal zone (synthetic)",
    },
    {
        "code": "RIM",
        "name": "Road Infrastructure and Maintenance Department",
        "category": ComplaintCategory.ROAD_MAINTENANCE,
        "description": (
            "Prototype unit responsible for pothole repair, resurfacing, "
            "footpath restoration and storm-water drain covers."
        ),
        "contact_email": "roads.maintenance@civicbridge.prototype",
        "contact_phone": "+91-44-0000-1002",
        "office_address": "Prototype Civic Centre, Zone 2, Synthetic City",
        "jurisdiction": "Prototype municipal zone (synthetic)",
    },
    {
        "code": "WSS",
        "name": "Water Supply and Sewerage Department",
        "category": ComplaintCategory.WATER_SUPPLY,
        "description": (
            "Prototype unit responsible for drinking water supply, pipeline "
            "leaks, tanker delivery and sewerage overflow."
        ),
        "contact_email": "water.supply@civicbridge.prototype",
        "contact_phone": "+91-44-0000-1003",
        "office_address": "Prototype Civic Centre, Zone 3, Synthetic City",
        "jurisdiction": "Prototype municipal zone (synthetic)",
    },
    {
        "code": "SLE",
        "name": "Street Lighting and Electrical Department",
        "category": ComplaintCategory.STREET_LIGHTING,
        "description": (
            "Prototype unit responsible for street light repair, new pole "
            "installation, cabling faults and dark-spot remediation."
        ),
        "contact_email": "street.lighting@civicbridge.prototype",
        "contact_phone": "+91-44-0000-1004",
        "office_address": "Prototype Civic Centre, Zone 4, Synthetic City",
        "jurisdiction": "Prototype municipal zone (synthetic)",
    },
)

#: Prototype resolution playbooks, one per category, keyed by department code.
SERVICE_GUIDELINES: tuple[dict[str, Any], ...] = (
    {
        "code": "GDL-SWM-001",
        "department_code": "SWM",
        "category": ComplaintCategory.WASTE_MANAGEMENT,
        "title": "Waste collection and bin clearance standard",
        "description": (
            "Playbook for missed household collection, overflowing bins and uncleared black spots."
        ),
        "sla_hours": 48,
        "applicable_priority": "medium",
        "resolution_steps": [
            "Log the reported bin or street and confirm the collection beat.",
            "Dispatch the beat vehicle and record arrival time.",
            "Clear the accumulated waste and disinfect the spot.",
            "Photograph the cleared area and close the complaint.",
        ],
        "escalation_matrix": {
            "level_1": {"after_hours": 48, "notify": "Assistant Engineer (Sanitation)"},
            "level_2": {"after_hours": 96, "notify": "Zonal Sanitation Officer"},
        },
        "required_evidence": ["photo_of_accumulated_waste", "landmark_or_ward_number"],
        "citizen_response_template": (
            "Your waste collection complaint has been registered. The collection "
            "beat has been alerted and a clearance crew is scheduled within {sla_hours} hours."
        ),
    },
    {
        "code": "GDL-RIM-001",
        "department_code": "RIM",
        "category": ComplaintCategory.ROAD_MAINTENANCE,
        "title": "Pothole and road surface repair standard",
        "description": ("Playbook for pothole filling, patch repair and footpath restoration."),
        "sla_hours": 72,
        "applicable_priority": "high",
        "resolution_steps": [
            "Inspect the reported stretch and measure the defect.",
            "Barricade the risk area until repair is completed.",
            "Carry out the patch or resurfacing work.",
            "Re-inspect and record the post-repair condition.",
        ],
        "escalation_matrix": {
            "level_1": {"after_hours": 72, "notify": "Assistant Engineer (Roads)"},
            "level_2": {"after_hours": 168, "notify": "Executive Engineer (Roads)"},
        },
        "required_evidence": ["photo_of_defect", "approximate_stretch_length_metres"],
        "citizen_response_template": (
            "Your road maintenance complaint has been registered. A survey team "
            "will inspect the location and repair it within {sla_hours} hours."
        ),
    },
    {
        "code": "GDL-WSS-001",
        "department_code": "WSS",
        "category": ComplaintCategory.WATER_SUPPLY,
        "title": "Drinking water supply restoration standard",
        "description": (
            "Playbook for supply interruption, low pressure, pipeline leaks and "
            "contaminated water reports."
        ),
        "sla_hours": 24,
        "applicable_priority": "critical",
        "resolution_steps": [
            "Verify the affected pipeline segment and valve status.",
            "Isolate the leak or fault and start repair.",
            "Arrange a water tanker if restoration exceeds the SLA.",
            "Restore supply, flush the line and confirm water quality.",
        ],
        "escalation_matrix": {
            "level_1": {"after_hours": 24, "notify": "Assistant Engineer (Water Supply)"},
            "level_2": {"after_hours": 48, "notify": "Zonal Water Supply Officer"},
        },
        "required_evidence": ["photo_or_video_of_issue", "supply_timings_observed"],
        "citizen_response_template": (
            "Your water supply complaint has been registered. A maintenance crew "
            "has been assigned and supply will be restored within {sla_hours} hours."
        ),
    },
    {
        "code": "GDL-SLE-001",
        "department_code": "SLE",
        "category": ComplaintCategory.STREET_LIGHTING,
        "title": "Street light repair and dark-spot remediation standard",
        "description": (
            "Playbook for non-functional lights, flickering fittings, damaged "
            "poles and unlit stretches."
        ),
        "sla_hours": 48,
        "applicable_priority": "medium",
        "resolution_steps": [
            "Locate the pole and identify the circuit feeding it.",
            "Replace the lamp, driver or fuse as required.",
            "Test the fitting at dusk and confirm illumination.",
            "Record the pole number and close the complaint.",
        ],
        "escalation_matrix": {
            "level_1": {"after_hours": 48, "notify": "Assistant Engineer (Electrical)"},
            "level_2": {"after_hours": 96, "notify": "Zonal Electrical Officer"},
        },
        "required_evidence": ["photo_of_pole_or_light", "pole_number_if_visible"],
        "citizen_response_template": (
            "Your street lighting complaint has been registered. The electrical "
            "wing will attend to the reported pole within {sla_hours} hours."
        ),
    },
)


def department_by_code() -> dict[str, dict[str, Any]]:
    """Index the prototype directory by its short code."""
    return {dept["code"]: dept for dept in DEPARTMENT_DIRECTORY}


def guideline_by_department_code() -> dict[str, dict[str, Any]]:
    """Index the prototype guidelines by the department code they belong to."""
    return {guide["department_code"]: guide for guide in SERVICE_GUIDELINES}
