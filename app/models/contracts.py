"""New contracts: no historical field compatibility is claimed.

Immutable tuples avoid nested mutable payloads. Revalidate at every boundary;
Pydantic model_copy(update=...) is deliberately not used for validated updates.
"""
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Step = Literal["history_analysis", "clinical_analysis"]
FindingCode = Literal["HISTORY_PRESENT", "HISTORY_MISSING", "OBSERVATIONS_PRESENT", "OBSERVATIONS_MISSING", "SYNTHETIC_ONLY"]
StopCode = Literal["REVIEW_REQUIRED", "MISSING_DATA", "UNKNOWN_ROUTE", "AGENT_ERROR", "INVALID_RESULT", "TIMEOUT", "CANCELLED", "STEP_LIMIT", "INVALID_PLAN", "INTERNAL_ERROR",
                   # P4, additive: execution found unfinished after a process restart.
                   "INTERRUPTED"]


def new_id() -> str:
    return uuid4().hex


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")
    schema_version: Literal["medrixa.prototype.v1"] = "medrixa.prototype.v1"


class Observation(Contract):
    code: Identifier
    value: Annotated[float, Field(allow_inf_nan=False)]
    unit: Identifier
    source: Literal["synthetic_fixture"] = "synthetic_fixture"


class ClinicalCase(Contract):
    case_id: Identifier = Field(default_factory=new_id)
    synthetic: Literal[True]
    source: Literal["synthetic_fixture"]
    history_available: bool
    observations: Annotated[tuple[Observation, ...], Field(max_length=100)] = ()

    @field_validator("synthetic", mode="before")
    @classmethod
    def explicit_synthetic_boolean(cls, value: object) -> object:
        if value is not True:
            raise ValueError("synthetic must be explicit boolean True")
        return value

    @model_validator(mode="after")
    def validate_case(self) -> "ClinicalCase":
        # Literal[True] alone may accept equality-compatible integer 1.
        if self.synthetic is not True:
            raise ValueError("synthetic must be the boolean True")
        codes = [item.code for item in self.observations]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate observation codes")
        return self


class Finding(Contract):
    code: FindingCode
    source: Literal["synthetic_completeness_rule"] = "synthetic_completeness_rule"
    rule_id: Identifier


class AgentMessage(Contract):
    message_id: Identifier = Field(default_factory=new_id)
    case_id: Identifier
    run_id: Identifier
    agent_id: Identifier
    step: Step


class AgentResult(Contract):
    result_id: Identifier = Field(default_factory=new_id)
    message_id: Identifier
    case_id: Identifier
    run_id: Identifier
    agent_id: Identifier
    status: Literal["completed", "abstained", "failed"]
    findings: Annotated[tuple[Finding, ...], Field(max_length=100)] = ()
    requires_human_review: Literal[True] = True

    @field_validator("requires_human_review", mode="before")
    @classmethod
    def explicit_review_boolean(cls, value: object) -> object:
        if value is not True:
            raise ValueError("review flag must be boolean True")
        return value

    @model_validator(mode="after")
    def review_is_mandatory(self) -> "AgentResult":
        if self.requires_human_review is not True:
            raise ValueError("human review is mandatory")
        return self


class AgentContext(Contract):
    case: ClinicalCase
    message: AgentMessage
    prior_results: Annotated[tuple[AgentResult, ...], Field(max_length=10)] = ()

    @model_validator(mode="after")
    def scope_matches(self) -> "AgentContext":
        if self.message.case_id != self.case.case_id:
            raise ValueError("message belongs to another case")
        for result in self.prior_results:
            if result.case_id != self.case.case_id or result.run_id != self.message.run_id:
                raise ValueError("prior result belongs to another case or run")
        return self
