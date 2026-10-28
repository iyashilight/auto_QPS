from qps.agents.runtime import AgentSubsystem


def test_disabling_agent_does_not_change_live_state() -> None:
    assert AgentSubsystem().stop()["live_impact"] is False
