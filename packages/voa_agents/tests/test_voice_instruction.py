"""The voice instruction keeps patient-facing times 12-hour."""

from voa_agents.voice.instruction import build_instruction
from voa_core.campaign import load_campaign


def test_the_agent_is_told_to_say_times_the_12_hour_way():
    text = build_instruction(
        load_campaign("post_op"),
        org_name="Example Clinic",
        patient={"first_name": "Jordan", "last_name": "Rivera", "is_diabetic": False},
    )
    assert "12-hour way" in text and "never 24-hour time" in text
