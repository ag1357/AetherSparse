from aethersparse.controller.search import canonical_answer_match, canonicalize


def test_percent_canonicalization_is_consistent_inside_comparisons() -> None:
    assert canonicalize("01.0162%") == "01.0162 %"
    comparison = canonicalize("01.0162% compared with 1337%.")

    assert canonicalize("01.0162%") in comparison
    assert canonicalize("1337%") in comparison
    assert canonical_answer_match(
        ("01.0162% < 1337%.",),
        ("01.0162% compared with 1337%.", "01.0162% < 1337%."),
    )
