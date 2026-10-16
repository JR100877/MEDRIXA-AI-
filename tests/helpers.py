from app.models.contracts import AgentMessage, AgentResult, ClinicalCase, Observation


def case(case_id="synthetic-case-1", history=True, observations=True):
    return ClinicalCase(case_id=case_id, history_available=history,
                        observations=(Observation(code="demo-measure", value=1.0, unit="demo-unit"),) if observations else ())


def message(state, **overrides):
    data = dict(case_id=state.case.case_id, run_id=state.run_id, agent_id="history-simulator", step="history_analysis")
    data.update(overrides)
    return AgentMessage(**data)


def result(request, **overrides):
    data = dict(message_id=request.message_id, case_id=request.case_id, run_id=request.run_id,
                agent_id=request.agent_id, status="completed")
    data.update(overrides)
    return AgentResult(**data)
