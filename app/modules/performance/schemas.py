import uuid
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.core.schemas import CamelModel, InputModel
from app.core.validators import OptionalText500
from app.modules.performance.models import STANDARD_GOALS, Skill, StrongFoot
from app.modules.reference.schemas import RefItem

Rating = Annotated[int, Field(ge=1, le=5)]


class SkillIn(InputModel):
    skill: Skill
    rating: Rating
    comment: OptionalText500 = None


class PerformanceReportIn(InputModel):
    student_id: uuid.UUID
    report_period: str = Field(min_length=1, max_length=60)
    recorded_date: date
    position: Annotated[str | None, Field(max_length=60)] = None
    strong_foot: StrongFoot
    skills: Annotated[list[SkillIn], Field(min_length=len(Skill), max_length=len(Skill))]
    strengths: str = Field(min_length=1, max_length=2000)
    areas_for_improvement: str = Field(min_length=1, max_length=2000)
    development_goals: Annotated[list[str], Field(default_factory=list, max_length=len(STANDARD_GOALS))]
    custom_goal: Annotated[str | None, Field(max_length=300)] = None
    coach_remarks: str = Field(min_length=1, max_length=2000)
    overall_rating: Rating

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if {s.skill for s in self.skills} != set(Skill):
            raise ValueError("Rate each of the 15 skills exactly once")
        unknown = set(self.development_goals) - set(STANDARD_GOALS)
        if unknown:
            raise ValueError(f"Unknown development goals: {', '.join(sorted(unknown))}")
        return self


class SkillOut(CamelModel):
    skill: Skill
    label: str
    rating: int
    comment: str | None


class ReportStudent(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    date_of_birth: date
    age: int


class PerformanceReportSummary(CamelModel):
    id: uuid.UUID
    student: RefItem
    coach: RefItem
    report_period: str
    recorded_date: date
    position: str | None
    overall_rating: int
    can_edit: bool = False


class PerformanceReportDetail(CamelModel):
    id: uuid.UUID
    student: ReportStudent
    coach: RefItem
    report_period: str
    recorded_date: date
    position: str | None
    strong_foot: StrongFoot
    skills: list[SkillOut]
    strengths: str
    areas_for_improvement: str
    development_goals: list[str]
    custom_goal: str | None
    coach_remarks: str
    overall_rating: int
    can_edit: bool
    created_at: datetime
    updated_at: datetime


class SkillLabel(CamelModel):
    key: Skill
    label: str


class PerformanceConfig(CamelModel):
    skills: list[SkillLabel]
    standard_goals: list[str]
    positions: list[str]
