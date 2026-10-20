# ruff: noqa: E501  (golden strings are kept on one line so a diff shows the whole message)
"""Golden outbound SMS text for the questionnaire.

The questionnaire wording moved from Python (voa_core.copy) into
config/campaigns/post_op.yaml. These strings were captured from the code BEFORE
that move; every outbound message must stay byte-identical. If you change the
wording on purpose, update the expected strings here in the same change (and
check whether the A2P campaign's sample messages need re-registering).
"""

import datetime

import pytest
from voa_core import copy as messages
from voa_core.copy import SiteCopy
from voa_core.sms import conversation
from voa_core.sms import states as st
from voa_core.sms.conversation import ConversationPolicy

SITE = SiteCopy(
    org_name="Acme Health", support_email="support@example.org", voice_number="+12025550142"
)
NOW = datetime.datetime(2026, 8, 17, 10, 0, tzinfo=datetime.UTC)


def _walk(replies, diabetic):
    """Every outbound message, per inbound reply: [reply, *extra_replies]."""
    s = {
        "state": st.STATE_AWAITING_CHOICE,
        "patient_context": {
            "patient_name": "Jordan Avery",
            "patient_id": "PT-1",
            "patient_dob": "1960-06-15",
            "is_diabetic": diabetic,
        },
        "patient_name": "Jordan Avery",
        "call_sid": "CA1",
        "retry_count": 0,
    }
    out = []
    for body in replies:
        d = conversation.decide(
            s, body, NOW, site=SITE, policy=ConversationPolicy(survey_enabled=True)
        )
        if d.session_patch:
            s.update(d.session_patch)
        if d.retry is not None:
            s["retry_count"] = d.retry
        if d.state:
            s["state"] = d.state
        out.append((body, [d.reply, *d.extra_replies]))
    return out


GOLDEN = {
    "diabetic_full": (
        True,
        [
            (
                "3",
                [
                    "Before we start, please confirm the date of birth for Jordan. Reply in the format MM/DD/YYYY."
                ],
            ),
            (
                "01/01/1990",
                ["Sorry, I couldn't read that as a date. Please reply in the format MM/DD/YYYY."],
            ),
            (
                "06/15/1960",
                [
                    "Thank you, that's verified. I'll send 8 short questions, one at a time. Just reply to each one. Reply C at any point to stop.",
                    "(1/8) How are you feeling overall since your procedure?",
                ],
            ),
            ("my incision is sore", ["(2/8) Are you having any pain right now? Reply YES or NO."]),
            (
                "maybe",
                [
                    "Sorry, I didn't catch that. (2/8) Are you having any pain right now? Reply YES or NO."
                ],
            ),
            (
                "yes, around my incision",
                [
                    "On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            (
                "lots",
                [
                    "Sorry, I didn't catch that. On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            (
                "6",
                [
                    "You mentioned pain at your incision. Is there pain anywhere else? Reply NO, or tell us where and what it feels like."
                ],
            ),
            ("no", ["Is it getting better, staying the same, or getting worse?"]),
            (
                "same",
                [
                    "(3/8) Have you had a fever, or a temperature above 100.4F, since you were discharged? Reply YES or NO."
                ],
            ),
            (
                "no",
                [
                    "(4/8) Is there anything unusual about your incision or bandage - spreading redness, drainage, warmth, opening, or a bad smell? Reply YES or NO."
                ],
            ),
            ("yes", ["Can you describe what you're seeing?"]),
            ("a bit red", ["(5/8) Are your legs or feet swollen? Reply YES or NO."]),
            ("yes, my left foot", ["Is the swelling getting worse? Reply YES or NO."]),
            ("no", ["(6/8) Are you sleeping well? Reply YES or NO."]),
            (
                "no",
                [
                    '(7/8) What is the range of your blood sugars? Reply with your numbers, like "110 to 160", or say if they have been high or low.'
                ],
            ),
            (
                "120 to 250",
                [
                    "(8/8) Do you have any questions for the care team? Reply NO, or type your question."
                ],
            ),
            ("yes", ["What is your question? Please type it in one message."]),
            (
                "When can I drive again",
                [
                    'Got it: "When can I drive again". Any other questions? Reply with your question, or NO if that\'s all.'
                ],
            ),
            (
                "Can I shower",
                [
                    'Got it: "Can I shower". Any other questions? Reply with your question, or NO if that\'s all.'
                ],
            ),
            ("no", [None]),
        ],
    ),
    "plain_followups": (
        False,
        [
            (
                "3",
                [
                    "Before we start, please confirm the date of birth for Jordan. Reply in the format MM/DD/YYYY."
                ],
            ),
            (
                "06/15/1960",
                [
                    "Thank you, that's verified. I'll send 7 short questions, one at a time. Just reply to each one. Reply C at any point to stop.",
                    "(1/7) How are you feeling overall since your procedure?",
                ],
            ),
            ("ok", ["(2/7) Are you having any pain right now? Reply YES or NO."]),
            (
                "yes",
                [
                    "On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            ("4", ["Please describe the pain - where is it, and what does it feel like?"]),
            ("sharp in my knee", ["Is it getting better, staying the same, or getting worse?"]),
            (
                "worse",
                [
                    "(3/7) Have you had a fever, or a temperature above 100.4F, since you were discharged? Reply YES or NO."
                ],
            ),
            (
                "no",
                [
                    "(4/7) Is there anything unusual about your incision or bandage - spreading redness, drainage, warmth, opening, or a bad smell? Reply YES or NO."
                ],
            ),
            ("no", ["(5/7) Are your legs or feet swollen? Reply YES or NO."]),
            ("yes and it's getting worse", ["Is it in one leg or foot, or both?"]),
            ("both", ["(6/7) Are you sleeping well? Reply YES or NO."]),
            (
                "yes",
                [
                    "(7/7) Do you have any questions for the care team? Reply NO, or type your question."
                ],
            ),
            ("no", [None]),
        ],
    ),
    "swelling_plain": (
        False,
        [
            (
                "3",
                [
                    "Before we start, please confirm the date of birth for Jordan. Reply in the format MM/DD/YYYY."
                ],
            ),
            (
                "06/15/1960",
                [
                    "Thank you, that's verified. I'll send 7 short questions, one at a time. Just reply to each one. Reply C at any point to stop.",
                    "(1/7) How are you feeling overall since your procedure?",
                ],
            ),
            ("fine", ["(2/7) Are you having any pain right now? Reply YES or NO."]),
            (
                "no",
                [
                    "(3/7) Have you had a fever, or a temperature above 100.4F, since you were discharged? Reply YES or NO."
                ],
            ),
            (
                "no",
                [
                    "(4/7) Is there anything unusual about your incision or bandage - spreading redness, drainage, warmth, opening, or a bad smell? Reply YES or NO."
                ],
            ),
            ("no", ["(5/7) Are your legs or feet swollen? Reply YES or NO."]),
            ("yes", ["Is it one leg or both, and is it getting worse?"]),
            ("both ankles", ["(6/7) Are you sleeping well? Reply YES or NO."]),
            (
                "yes",
                [
                    "(7/7) Do you have any questions for the care team? Reply NO, or type your question."
                ],
            ),
            (
                "my question is about driving and stuff",
                [
                    'Got it: "my question is about driving and stuff". Any other questions? Reply with your question, or NO if that\'s all.'
                ],
            ),
            (
                "what about bathing",
                [
                    'Got it: "what about bathing". Any other questions? Reply with your question, or NO if that\'s all.'
                ],
            ),
            (
                "how about stairs",
                [
                    'Got it: "how about stairs". Any other questions? Reply with your question, or NO if that\'s all.'
                ],
            ),
            (
                "when is my visit",
                [
                    'Got it: "when is my visit". Any other questions? Reply with your question, or NO if that\'s all.'
                ],
            ),
            (
                "can I drive",
                [
                    'Got it: "can I drive". That\'s as many as we can take by text - your care team can help with anything else.'
                ],
            ),
        ],
    ),
    "stopped": (
        False,
        [
            (
                "3",
                [
                    "Before we start, please confirm the date of birth for Jordan. Reply in the format MM/DD/YYYY."
                ],
            ),
            (
                "06/15/1960",
                [
                    "Thank you, that's verified. I'll send 7 short questions, one at a time. Just reply to each one. Reply C at any point to stop.",
                    "(1/7) How are you feeling overall since your procedure?",
                ],
            ),
            ("fine", ["(2/7) Are you having any pain right now? Reply YES or NO."]),
            (
                "C",
                [
                    "No problem, we've stopped the questions. Your care team can follow up with you directly."
                ],
            ),
        ],
    ),
    "cut_short": (
        False,
        [
            (
                "3",
                [
                    "Before we start, please confirm the date of birth for Jordan. Reply in the format MM/DD/YYYY."
                ],
            ),
            (
                "06/15/1960",
                [
                    "Thank you, that's verified. I'll send 7 short questions, one at a time. Just reply to each one. Reply C at any point to stop.",
                    "(1/7) How are you feeling overall since your procedure?",
                ],
            ),
            ("fine", ["(2/7) Are you having any pain right now? Reply YES or NO."]),
            (
                "yes",
                [
                    "On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            (
                "x",
                [
                    "Sorry, I didn't catch that. On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            (
                "x",
                [
                    "Sorry, I didn't catch that. On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            (
                "x",
                [
                    "Sorry, I didn't catch that. On a scale of 0 to 10, where 10 is the worst pain imaginable, what number would you give it right now?"
                ],
            ),
            (
                "x",
                [
                    "Thanks - we'll stop there. Your care team has what you've told us and can follow up with you directly. In an emergency, call 911."
                ],
            ),
        ],
    ),
}

GOLDEN_CLOSINGS = [
    "That's everything - thank you. Your care team has your answers. Please bring a list of your current medications to your post-op follow-up clinic visit. If anything changes or gets worse, call your care team, or 911 in an emergency.",
    "Thank you - that's everything. Based on your answers someone from your care team will reach out to you. Please bring a list of your current medications to your post-op follow-up clinic visit. If things get worse before then, call your care team, or 911 in an emergency.",
    "That's everything - thank you. Your care team has your answers. Your care team will follow up to get your questions answered. Please bring a list of your current medications to your post-op follow-up clinic visit. If anything changes or gets worse, call your care team, or 911 in an emergency.",
    "Thank you - that's everything. Based on your answers someone from your care team will reach out to you. Your care team will follow up to get your questions answered. Please bring a list of your current medications to your post-op follow-up clinic visit. If things get worse before then, call your care team, or 911 in an emergency.",
    "Thank you for your answers. We had a problem recording them - please call the number on your paperwork so your care team has them.",
]


@pytest.mark.parametrize("name", list(GOLDEN))
def test_outbound_text_is_unchanged(name):
    diabetic, expected = GOLDEN[name]
    got = _walk([body for body, _ in expected], diabetic)
    for (body, want), (_, have) in zip(expected, got, strict=True):
        assert have == want, body


def test_closings_are_unchanged():
    assert [
        messages.survey_done_routine(),
        messages.survey_done_urgent(),
        messages.survey_done_routine(asked_questions=True),
        messages.survey_done_urgent(asked_questions=True),
        messages.survey_file_failed(),
    ] == GOLDEN_CLOSINGS
