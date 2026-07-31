from aethersparse.cells.address import (
    AddressCandidate,
    GenerativeAddressGate,
    address_gate_metrics,
)


def test_generated_addresses_are_hints_and_invalid_ids_fail_closed() -> None:
    gate = GenerativeAddressGate({"cell:moon", "cell:tide"})
    decision = gate.decide(
        (
            AddressCandidate(cell_id="cell:invented", score=0.99),
            AddressCandidate(cell_id="cell:moon", score=0.92),
        )
    )
    assert decision.accepted_cell_ids == ("cell:moon",)
    assert decision.rejected_cell_ids == ("cell:invented",)
    assert not decision.fallback_required
    report = address_gate_metrics(((decision, {"cell:moon"}),))
    assert report["valid_id_top8_recall"] == 1.0
    assert report["invalid_ids_reaching_retrieval"] == 0


def test_unknown_and_ambiguous_entities_force_fallback() -> None:
    gate = GenerativeAddressGate({"cell:a", "cell:b"})
    candidates = (
        AddressCandidate(cell_id="cell:a", score=0.90),
        AddressCandidate(cell_id="cell:b", score=0.88),
    )
    assert gate.decide(candidates).reason == "AMBIGUOUS"
    assert gate.decide(candidates, unknown_entity_present=True).reason == "UNKNOWN_ENTITY"
