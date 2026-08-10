

class TestSchemaReservations:
    """Mission 4 forward-compatibility schema (landed as schema, not capability)."""

    def test_entity_id_bands_reserved(self) -> None:
        from aethersparse.controller.models import (
            CORPUS_ENTITY_ID_PREFIX,
            USER_ENTITY_ID_PREFIX,
        )

        assert CORPUS_ENTITY_ID_PREFIX == "as:v050:entity:"
        assert USER_ENTITY_ID_PREFIX == "as:user:entity:"
        assert CORPUS_ENTITY_ID_PREFIX != USER_ENTITY_ID_PREFIX

    def test_span_source_class_defaults_corpus(self) -> None:
        from aethersparse.controller.models import ExactSourceSpan

        span = ExactSourceSpan(
            span_id="s1",
            document_id="mw:1:2",
            source_title="T",
            source_revision="2",
            source_url="u",
            source_family="wikipedia",
            char_start=0,
            char_end=1,
            text="x",
            text_hash="h",
        )
        assert span.source_class == "CORPUS"
        assert ExactSourceSpan.model_fields["source_class"].default == "CORPUS"

    def test_claim_grounding_defaults_corpus_grounded(self) -> None:
        from aethersparse.controller.models import StructuredClaim

        claim = StructuredClaim(
            claim_id="c1",
            subject_entity_id="as:v050:entity:x",
            relation_family="r",
            object_value="v",
            answer_shape="entity",
            source_span_ids=("s1",),
        )
        assert claim.grounding == "CORPUS_GROUNDED"
        assert (
            StructuredClaim.model_fields["grounding"].default == "CORPUS_GROUNDED"
        )
