"""Wave 4 lane E (ADR-067): the individual's own text recognised by what it emitted, not by how a page credits it.

EXPECTATIONS below were written and committed BEFORE the mechanism (webcortex/emitted.py) and before any case that
checks them: each names what must hold with the setting `own_text_provenance` on (and, where said, off). The cases
are added under them afterwards; an expectation is never edited to fit a result (a changed one is a new entry).
"""

EXPECTATIONS = {
    # the rule
    "own_unsourced_value_echoed": "the individual emitted a value for a subject that no source it had read stated; "
                                  "pages read later, published after the emission, stating that value for that "
                                  "subject are not evidence for it: verbatim, paraphrased, with the number written "
                                  "another way (2.4 / v2.4.0), crediting a model or crediting nobody; nothing is "
                                  "admitted on them and each is named as an echo",
    "sourced_emission_is_not_an_echo": "a value the individual emitted while a source it had read stated it is not an "
                                       "echo: later pages stating it count as before",
    "page_published_before_the_emission_counts": "a page whose stated publication precedes the emission is never its "
                                                 "echo, even when it is read after it",
    "other_subject_unaffected": "an emitted value of one subject never discards a page stating the same value of "
                                "another subject",
    "disagreeing_echo_still_counts_against": "an echo that disagrees with what the sources say stays on the "
                                             "contradicting side (counts against a held value only as today)",
    "template_answer_is_not_own_text": "an extractive (template) answer quotes sources: nothing of it is recorded",
    # security
    "hostile_quote_cannot_discard_real_evidence": "a page that quotes the individual's own answer, or any text, cannot "
                                                  "make another page's claim count for less: honest pages stating "
                                                  "another value are admitted as with the setting off",
    "pages_cannot_write_fingerprints": "only the individual's own emissions record fingerprints; reading pages records "
                                       "none",
    "fingerprints_hold_no_text": "what is recorded is salted digests of (subject name, normalised value) with the time "
                                 "and whether a source supported it; never the answer's text, never a source's name",
    "lookup_is_bounded_and_outside_the_write_transaction": "the consolidation's check is point lookups by digest, made "
                                                           "before the write transaction; a claim past the cap is "
                                                           "not an echo and is named in echo_skipped and logged with "
                                                           "ids only",
    "emission_bounded": "at most a fixed number of fingerprints per answer and per subject-value; the store has a cap, "
                        "and a full store records nothing more, visibly",
    "never_in_family_or_agent_scope": "Pocket's family views, the family self-description and the MCP surface never "
                                      "show a fingerprint",
    "failures_keep_the_answer": "a failing record or check never costs the owner the answer or the consolidation: the "
                                "task's error event names the type only",
    # forget and restore
    "forget_source_keeps_the_emission_sourced": "forgetting the source the emitted value rested on keeps the emission "
                                                "sourced (security review M3), and keeps nothing of the source",
    "forget_research_removes_its_fingerprints": "forgetting the task that emitted removes its fingerprints; echo pages "
                                                "then count as with the setting off",
    "forget_knowledge_removes_the_subject_values": "forgetting a knowledge item removes the fingerprints of its subject "
                                                   "and values",
    "forget_voice_session_reaches_them": "forgetting a voice session removes the fingerprints of the research it created",
    "restore_replays_the_forgets": "a checkpoint restored after a forget holds none of the forgotten fingerprints",
    # the default
    "off_changes_nothing": "with the setting off nothing is recorded and consolidation is as before",
    # residual, recorded so it is not mistaken for coverage
    "residual_backdated_page_counts": "a page that states a publication date before the emission counts, whatever it "
                                      "repeats (dates are the page's own claim; not closed)",
}
