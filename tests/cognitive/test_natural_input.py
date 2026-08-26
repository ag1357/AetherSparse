import pytest

from aethersparse.cognitive.natural_input import (
    NaturalRequestKind,
    classify_natural_input,
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Could you tell me where Alan Turing was born?", NaturalRequestKind.QUESTION),
        ("What is uFnnel?", NaturalRequestKind.QUESTION),
        ("Where was he born?", NaturalRequestKind.FOLLOW_UP),
        ("What about Ada Lovelace?", NaturalRequestKind.FOLLOW_UP),
        ("Actually, I meant the other Mercury.", NaturalRequestKind.CORRECTION),
        ("Cancel that task", NaturalRequestKind.CANCEL),
        ("Start over", NaturalRequestKind.RESET),
        ("Remember that I prefer metric units", NaturalRequestKind.MEMORY_TASK),
        ("Delete that memory", NaturalRequestKind.MEMORY_TASK),
        ("Inspect the source tree and its callers", NaturalRequestKind.SOURCE_TASK),
        ("Run the available diagnostic", NaturalRequestKind.TOOL_TASK),
        ("Sing a song", NaturalRequestKind.UNSUPPORTED),
    ],
)
def test_natural_request_classes(text: str, kind: NaturalRequestKind) -> None:
    assert classify_natural_input(text).request_kind is kind


def test_natural_signals_cover_constraints_and_long_tasks() -> None:
    signals = classify_natural_input(
        "The source contradicts it, but do not stop; iterate until tests pass."
    )
    assert signals.has_pronoun_reference
    assert signals.has_negation
    assert signals.has_contradiction_signal
    assert signals.is_multi_clause
    assert signals.is_long_running_instruction


def test_empty_input_fails_closed() -> None:
    with pytest.raises(ValueError):
        classify_natural_input("   ")


@pytest.mark.parametrize(
    ("phenomenon", "text"),
    [
        ("paraphrase", "Would you happen to know Turing's birthplace?"),
        ("typo", "Wher was Alan Turing born?"),
        ("misspelling", "What is uFnnel?"),
        ("indirect", "I wonder when the bridge opened."),
        ("multi_clause", "Find the date, then explain its source."),
        ("pronoun", "Where was he born?"),
        ("what_about", "What about Ada Lovelace?"),
        ("correction", "Actually, I meant Mercury the element."),
        ("contradiction", "This source contradicts it."),
        ("negation", "Which result was not accepted?"),
        ("incorrect_premise", "Why did Turing win the 1970 award when he died in 1954?"),
        ("clarification", "Did you mean Mercury the planet or the element?"),
        ("cancel", "Cancel that task"),
        ("reset", "Reset"),
        ("follow_up", "And where did she work?"),
        ("tool_request", "Run the diagnostic"),
        ("memory_request", "Remember that I use metric units"),
        ("source_request", "Inspect the repository callers"),
        ("unsupported", "Take a photo"),
        ("unavailable_capability", "Run the unavailable camera tool"),
        ("long_running", "Inspect and iterate until the tests pass"),
    ],
)
def test_v15_natural_input_phenomena_are_bounded(phenomenon: str, text: str) -> None:
    signals = classify_natural_input(text)
    assert phenomenon
    assert signals.token_count >= 1
