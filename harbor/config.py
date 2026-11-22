"""Validated, code-owned synthetic practice packs; YAML is not executable policy."""

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
VERTICALS = ("dental", "physio", "clinic", "lab", "aesthetics", "fitness")


class Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class Service(Model):
    id: str
    name: str
    duration_minutes: int = Field(gt=0, le=480)
    buffer_minutes: int = Field(ge=0, le=120)
    skill: str
    resources: list[str]
    prerequisites: list[str]
    capacity: int = Field(ge=1, le=100)
    discreet: bool
    intake_template: str


class Location(Model):
    id: str
    name: str
    timezone: str
    hours: dict[str, list[str]]
    holidays: list[str]

    @model_validator(mode="after")
    def check_hours(self):
        from datetime import date, time

        ZoneInfo(self.timezone)
        for day, span in self.hours.items():
            if (
                day not in map(str, range(7))
                or len(span) != 2
                or time.fromisoformat(span[0]) >= time.fromisoformat(span[1])
            ):
                raise ValueError("Hours need weekday 0..6 and increasing same-day times")
        for day in self.holidays:
            date.fromisoformat(day)
        return self


class Scheduling(Model):
    min_lead_minutes: int = Field(ge=0)
    max_horizon_days: int = Field(gt=0, le=365)
    slot_step_minutes: int = Field(gt=0, le=60)
    hold_minutes: int = Field(gt=0, le=30)
    cancel_window_hours: int = Field(ge=0)
    late_cancel_staff_approval: bool
    fee_notice: str
    urgent_reserved_hours: list[int]


class Pack(Model):
    schema_version: Literal[1]
    id: str
    name: str
    vertical: Literal["dental", "physio", "clinic", "lab", "aesthetics", "fitness"]
    profile: Literal["us_demo"]
    location: Location
    scheduling: Scheduling
    providers: list[dict]
    resources: list[dict]
    services: list[Service]
    reminders: dict
    intake_templates: dict
    faq: list[dict]

    @model_validator(mode="after")
    def check_references(self):
        ids = [s.id for s in self.services]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate service ID")
        skills = {skill for p in self.providers for skill in p["skills"]}
        resource_types = {r["type"] for r in self.resources}
        for service in self.services:
            if service.skill not in skills or not set(service.resources) <= resource_types:
                raise ValueError("Service lacks compatible provider or resource")
            if service.intake_template not in self.intake_templates:
                raise ValueError("Missing intake template")
        if any(not f.get("source", "").startswith("config://") for f in self.faq):
            raise ValueError("FAQ needs a synthetic configuration citation")
        if any(h < 0 or h > 23 for h in self.scheduling.urgent_reserved_hours):
            raise ValueError("Invalid reserved hour")
        return self


def load_packs(path: Path | str = CONFIG_DIR) -> dict[str, dict]:
    root = Path(path)
    packs = {
        name: Pack.model_validate(yaml.safe_load((root / f"{name}.yaml").read_text())).model_dump()
        for name in VERTICALS
    }
    service_ids = [s["id"] for p in packs.values() for s in p["services"]]
    if len(service_ids) != len(set(service_ids)):
        raise ValueError("Service IDs must be globally unique")
    return packs


def load_profile(path: Path | str = CONFIG_DIR) -> dict:
    profile = yaml.safe_load((Path(path) / "us_demo.yaml").read_text())
    if profile.get("country") != "USA" or profile.get("status") != "synthetic_demonstration_not_legal_guidance":
        raise ValueError("Only the labeled US demonstration profile is supported")
    if not 1 <= profile["guardian_age"] <= 21 or not 1 <= profile["retention_days"] <= 3650:
        raise ValueError("Invalid demonstration policy range")
    return profile


def answer_faq(packs: dict, vertical: str, query: str) -> dict | None:
    """Transparent lexical retrieval, not embeddings; only authored text is returned."""
    import re

    words = set(re.findall(r"[a-z]+", query.lower())) - {"what", "is", "the", "your", "a", "i", "can", "do", "are"}
    ranked = [(len(words & set(re.findall(r"[a-z]+", f["question"].lower()))), f) for f in packs[vertical]["faq"]]
    score, hit = max(ranked, key=lambda pair: pair[0], default=(0, None))
    return {"answer": hit["answer"], "citation": hit["source"]} if score else None
