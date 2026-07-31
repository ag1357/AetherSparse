from aethersparse.cells.adversarial import (
    AdversarialMutator,
    BoundClaim,
    ExactClaimVerifier,
    mutation_rejection_report,
)


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
    report = mutation_rejection_report((claim,))
    assert report["mutation_count"] == 7
    assert report["rejection_rate"] == 1.0


def test_mutations_never_degenerate_to_the_original_claim() -> None:
    claim = BoundClaim(
        claim_id="claim:edge",
        subject_id="entity:x",
        relation_id="relation:x",
        object_value="2099",
        source_span_id="span:x",
        source_text="The value is 2099.",
        polarity=0,
        attribution_id="entity:wrong-speaker",
    )
    mutations = AdversarialMutator().mutate(claim)
    assert all(item.candidate != claim for item in mutations)
    assert all(not ExactClaimVerifier().verify(item.candidate, claim)[0] for item in mutations)
