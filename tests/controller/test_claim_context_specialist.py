from aethersparse.controller.claim_context_specialist import (
    CONTEXT_FEATURE_NAMES,
    QuantizedClaimContextSpecialist,
    claim_context_features,
)
from aethersparse.controller.micro_ops import MicroAction, MicroState


def _state(text: str, value: str, *, shape: str = "date") -> MicroState:
    return MicroState(
        case_id="case",
        frame={"answer_shape": shape, "requested_relation_families": [shape]},
        claims=(
            {
                "claim_id": "claim",
                "object_value": value,
                "quotation": value if shape == "quotation" else None,
                "confidence": 0.8,
                "source_span_ids": ["span"],
            },
        ),
        source_spans=({"span_id": "span", "text": text},),
        active_claim_ids=("claim",),
    )


def test_context_features_distinguish_narrative_date_from_reference_metadata() -> None:
    action = MicroAction(operation_id=43, arguments={"claim_id": "claim"})
    narrative = claim_context_features(
        _state("The film is set for release on May 28, 2027.", "May 28, 2027"), action
    )
    metadata = claim_context_features(
        _state("<ref access-date=2026-02-22>", "2026-02-22"), action
    )
    fields = dict(zip(CONTEXT_FEATURE_NAMES, narrative, strict=True))
    metadata_fields = dict(zip(CONTEXT_FEATURE_NAMES, metadata, strict=True))
    assert fields["narrative_relation_context"] == 1.0
    assert metadata_fields["metadata_date_context"] == 1.0
    assert metadata_fields["inside_reference"] == 1.0


def test_context_features_preserve_generic_quote_and_definition_signals() -> None:
    action = MicroAction(operation_id=43, arguments={"claim_id": "claim"})
    quoted = claim_context_features(
        _state('Source: "grounded words"', "grounded words", shape="quotation"), action
    )
    definition = claim_context_features(
        _state(
            "an object used for pouring liquids",
            "an object used for pouring liquids",
            shape="definition",
        ),
        action,
    )
    quote_fields = dict(zip(CONTEXT_FEATURE_NAMES, quoted, strict=True))
    definition_fields = dict(zip(CONTEXT_FEATURE_NAMES, definition, strict=True))
    assert quote_fields["quote_after_colon"] == 1.0
    assert definition_fields["definitional_form"] == 1.0
    assert definition_fields["span_value_only"] == 1.0


def test_quantized_specialist_has_sparse_active_head() -> None:
    specialist = QuantizedClaimContextSpecialist(
        weights_int8=tuple((0,) * len(CONTEXT_FEATURE_NAMES) for _ in range(3)),
        weight_scale=1.0,
        training_epochs=0,
    )
    assert specialist.parameter_count == 54
    assert len(specialist.feature_names) == 18
