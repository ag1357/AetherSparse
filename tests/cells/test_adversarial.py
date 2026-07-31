from aethersparse.cells.adversarial import AdversarialMutator, BoundClaim, ExactClaimVerifier


def test_every_adversarial_mutation_is_rejected_by_exact_ledger() -> None:
    claim = BoundClaim(
        claim_id="claim:1",
        subject_id="entity:apollo-11",
        relation_id="relation:landed-on",
        object_value="1969-07-20",
        source_span_id="span:1",
        source_text="Apollo 11 landed on July 20, 1969.",
        attribution_id="entity:nasa",
    )
    verifier = ExactClaimVerifier()
    mutations = AdversarialMutator().mutate(claim)
    assert len(mutations) == 7
    assert all(not verifier.verify(item.candidate, claim)[0] for item in mutations)
    assert verifier.verify(claim, claim) == (True, ())
