import uuid
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, CheckConstraint, Date, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import pg_enum

if TYPE_CHECKING:
    from app.modules.coaches.models import CoachProfile
    from app.modules.students.models import Student


class Skill(StrEnum):
    TECHNICAL_ABILITY = "TECHNICAL_ABILITY"
    TACTICAL_UNDERSTANDING = "TACTICAL_UNDERSTANDING"
    BALL_CONTROL_FIRST_TOUCH = "BALL_CONTROL_FIRST_TOUCH"
    PASSING = "PASSING"
    DRIBBLING = "DRIBBLING"
    SHOOTING_FINISHING = "SHOOTING_FINISHING"
    DEFENDING = "DEFENDING"
    DECISION_MAKING = "DECISION_MAKING"
    INDIVIDUAL_SKILLS = "INDIVIDUAL_SKILLS"
    TEAMWORK = "TEAMWORK"
    COMMUNICATION = "COMMUNICATION"
    HARD_WORK = "HARD_WORK"
    DISCIPLINE = "DISCIPLINE"
    CHARACTER_ATTITUDE = "CHARACTER_ATTITUDE"
    FITNESS = "FITNESS"


SKILL_LABELS: dict[Skill, str] = {
    Skill.TECHNICAL_ABILITY: "TECHNICAL ABILITY",
    Skill.TACTICAL_UNDERSTANDING: "TACTICAL UNDERSTANDING",
    Skill.BALL_CONTROL_FIRST_TOUCH: "BALL CONTROL / FIRST TOUCH",
    Skill.PASSING: "PASSING",
    Skill.DRIBBLING: "DRIBBLING",
    Skill.SHOOTING_FINISHING: "SHOOTING / FINISHING",
    Skill.DEFENDING: "DEFENDING",
    Skill.DECISION_MAKING: "DECISION MAKING",
    Skill.INDIVIDUAL_SKILLS: "INDIVIDUAL SKILLS",
    Skill.TEAMWORK: "TEAMWORK",
    Skill.COMMUNICATION: "COMMUNICATION",
    Skill.HARD_WORK: "HARD WORK",
    Skill.DISCIPLINE: "DISCIPLINE",
    Skill.CHARACTER_ATTITUDE: "CHARACTER & ATTITUDE",
    Skill.FITNESS: "FITNESS",
}

STANDARD_GOALS = (
    "Improve weak foot",
    "Improve first touch",
    "Improve passing accuracy",
    "Improve tactical awareness",
    "Improve finishing",
    "Improve fitness",
    "Improve communication",
)

POSITIONS = (
    "Goalkeeper",
    "Centre Back",
    "Full Back",
    "Defensive Midfielder",
    "Central Midfielder",
    "Attacking Midfielder",
    "Winger",
    "Striker",
)


class StrongFoot(StrEnum):
    RIGHT = "RIGHT"
    LEFT = "LEFT"
    BOTH = "BOTH"


class PerformanceReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "performance_reports"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    coach_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coach_profiles.id", ondelete="RESTRICT"), index=True
    )
    report_period: Mapped[str] = mapped_column(String(60))
    recorded_date: Mapped[date] = mapped_column(Date)
    position: Mapped[str | None] = mapped_column(String(60))
    strong_foot: Mapped[StrongFoot] = mapped_column(pg_enum(StrongFoot, "strong_foot"))
    strengths: Mapped[str] = mapped_column(Text)
    areas_for_improvement: Mapped[str] = mapped_column(Text)
    development_goals: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list, server_default="{}")
    custom_goal: Mapped[str | None] = mapped_column(String(300))
    coach_remarks: Mapped[str] = mapped_column(Text)
    overall_rating: Mapped[int] = mapped_column(SmallInteger)

    student: Mapped["Student"] = relationship(lazy="selectin")
    coach: Mapped["CoachProfile"] = relationship(lazy="selectin")
    skills: Mapped[list["PerformanceSkillRating"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="PerformanceSkillRating.skill"
    )

    __table_args__ = (
        UniqueConstraint("student_id", "report_period", name="uq_performance_reports_student_period"),
        CheckConstraint("overall_rating BETWEEN 1 AND 5", name="overall_rating_range"),
    )


class PerformanceSkillRating(Base):
    __tablename__ = "performance_skill_ratings"

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("performance_reports.id", ondelete="CASCADE"), primary_key=True
    )
    skill: Mapped[Skill] = mapped_column(pg_enum(Skill, "skill"), primary_key=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),)
