"""Known failure 5 (docs/LANGUAGES-PLAN.md §2): the knowledge base's keyword veto
was English only. Fictional data only."""


def test_5_the_knowledge_keyword_veto_reads_other_languages():
    from voa_knowledge.agent import output_veto
    from voa_knowledge.classify import R_LANGUAGE, R_RED_FLAG, keyword_gate

    # Before: "diet" passed the keyword gate to the LLM.
    c, reason = keyword_gate("¿Qué dieta debo seguir si tengo fiebre?")
    assert reason == R_LANGUAGE and c.vetoed_by == "keywords"
    assert keyword_gate("me duele el pecho, ¿qué hago?")[1] == R_RED_FLAG
    assert keyword_gate("When can I shower?")[1] is None
    # An answer drafted in another language is never said.
    assert output_veto("Puede ducharse después de 48 horas.")
    assert not output_veto("You can shower after 48 hours.")
