"""Immutable snapshot. Mutations belong exclusively to StateManager."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models.contracts import AgentMessage, AgentResult, ClinicalCase, Contract, Identifier, StopCode, new_id

EventKind = Literal["RUN_STARTED", "AGENT_DISPATCHED", "RESULT_ACCEPTED", "REVIEW_REQUESTED"]


class AuditEvent(Contract):
    sequence: Annotated[int, Field(ge=1)]
    case_id: Identifier
    run_id: Identifier
    kind: EventKind
    at: datetime
    message_id: Identifier | None = None
    reason: StopCode | None = None

    @model_validator(mode="after")
    def timezone_required(self) -> "AuditEvent":
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("audit time must be timezone aware")
        return self


class State(Contract):
    case: ClinicalCase
    run_id: Identifier = Field(default_factory=new_id)
    revision: Annotated[int, Field(ge=0)] = 0
    status: Literal["created", "running", "awaiting_human_review"] = "created"
    pending_message: AgentMessage | None = None
    messages: Annotated[tuple[AgentMessage, ...], Field(max_length=10)] = ()
    results: Annotated[tuple[AgentResult, ...], Field(max_length=10)] = ()
    events: tuple[AuditEvent, ...] = ()
    stop_reason: StopCode | None = None

    @model_validator(mode="after")
    def consistent_snapshot(self) -> "State":
        if self.revision != len(self.events):
            raise ValueError("revision and event count differ")
        for i, event in enumerate(self.events, 1):
            if event.sequence != i or event.case_id != self.case.case_id or event.run_id != self.run_id:
                raise ValueError("invalid audit scope or sequence")
        for items, id_field in ((self.messages, "message_id"), (self.results, "result_id")):
            identifiers = [getattr(item, id_field) for item in items]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("duplicate identifiers")
            if any(x.case_id != self.case.case_id or x.run_id != self.run_id for x in items):
                raise ValueError("cross-case or cross-run data")
        requests = {m.message_id: m for m in self.messages}
        if len({r.message_id for r in self.results}) != len(self.results):
            raise ValueError("multiple results for a request")
        for result in self.results:
            request = requests.get(result.message_id)
            if request is None or request.agent_id != result.agent_id:
                raise ValueError("result without matching request")
        if self.pending_message is not None:
            if self.status != "running" or requests.get(self.pending_message.message_id) != self.pending_message:
                raise ValueError("invalid pending request")
            if any(r.message_id == self.pending_message.message_id for r in self.results):
                raise ValueError("pending request already completed")
        if self.status == "created" and (self.revision or self.messages or self.results or self.stop_reason):
            raise ValueError("new state cannot have execution history")
        if self.status == "awaiting_human_review" and self.stop_reason is None:
            raise ValueError("review needs reason")
        if self.status != "awaiting_human_review" and self.stop_reason is not None:
            raise ValueError("stop reason outside review state")
        # Replay structural events: importing a snapshot must not create an
        # impossible running state or conceal an uncorrelated result.
        if self.status != "created" and not self.events:
            raise ValueError("execution requires a start event")
        message_index = result_index = 0
        active = None
        review_seen = False
        for index, event in enumerate(self.events):
            if review_seen:
                raise ValueError("events after terminal review")
            if event.kind != "REVIEW_REQUESTED" and event.reason is not None:
                raise ValueError("unexpected reason")
            if index == 0:
                if event.kind != "RUN_STARTED" or event.message_id is not None:
                    raise ValueError("first event must start the run")
                continue
            if event.kind == "AGENT_DISPATCHED":
                if active is not None or message_index >= len(self.messages):
                    raise ValueError("invalid dispatch sequence")
                active = self.messages[message_index].message_id
                if event.message_id != active:
                    raise ValueError("dispatch does not match message")
                message_index += 1
            elif event.kind == "RESULT_ACCEPTED":
                if active is None or result_index >= len(self.results):
                    raise ValueError("result accepted without active request")
                if event.message_id != active or self.results[result_index].message_id != active:
                    raise ValueError("result sequence mismatch")
                result_index += 1
                active = None
            elif event.kind == "REVIEW_REQUESTED":
                if event.reason != self.stop_reason or event.reason is None or event.message_id is not None:
                    raise ValueError("review event mismatch")
                review_seen = True
                active = None
            else:
                raise ValueError("duplicate start event")
        if message_index != len(self.messages) or result_index != len(self.results):
            raise ValueError("missing audit events")
        pending_id = self.pending_message.message_id if self.pending_message else None
        if active != pending_id or review_seen != (self.status == "awaiting_human_review"):
            raise ValueError("final state and audit disagree")
        return self
