from __future__ import annotations

from aethersparse.controller.framing import QueryFramer
from aethersparse.controller.linking import EntityRegistry
from aethersparse.controller.models import (
    AnswerShape,
    CanonicalEntity,
    EntityMention,
    RequiredFacet,
)


def _registry() -> EntityRegistry:
    return EntityRegistry(
        (
            CanonicalEntity(
                entity_id="entity:ada",
                title="Ada Lovelace",
                entity_types=("person",),
                redirects=("Augusta Ada King",),
                aliases=("Lovelace",),
                anchors=("Ada",),
                relation_families=("birth", "definition"),
            ),
            CanonicalEntity(
                entity_id="entity:mercury_planet",
                title="Mercury",
                entity_types=("planet",),
                aliases=("Mercury",),
                relation_families=("location",),
            ),
            CanonicalEntity(
                entity_id="entity:mercury_element",
                title="Mercury",
                entity_types=("element",),
                aliases=("Mercury",),
                relation_families=("quantity",),
            ),
        )
    )


def test_query_frame_exposes_shape_facets_constraints_and_discourse() -> None:
    frame = QueryFramer().frame(
        'When did Ada Lovelace say "analytical engine" in London in 1843, and was it earlier?',
        prior_entity_ids=("entity:ada",),
    )

    assert frame.answer_shape is AnswerShape.DATE
    assert RequiredFacet.TIME in frame.required_facets
    assert "1843" in frame.temporal_constraints
    assert "analytical engine" in frame.attribution_constraints
    assert frame.discourse_references[0].surface == "it"
    assert frame.discourse_references[0].antecedent_entity_ids == ("entity:ada",)

    attribution = QueryFramer().frame('Who said "analytical engine"?')
    assert attribution.answer_shape is AnswerShape.ENTITY
    assert RequiredFacet.SPEAKER in attribution.required_facets
    assert RequiredFacet.QUOTATION in attribution.required_facets


def test_linking_cascade_and_unknown_copy_are_fail_closed() -> None:
    registry = _registry()
    exact = registry.resolve_mention(
        EntityMention(surface="Ada Lovelace", char_start=0, char_end=12),
        query="Ada Lovelace birth",
        requested_relations=("birth",),
    )
    redirect = registry.resolve_mention(
        EntityMention(surface="Augusta Ada King", char_start=0, char_end=16),
        query="Augusta Ada King birth",
        requested_relations=("birth",),
    )
    unknown = EntityMention(surface="Qzzyxx-999", char_start=4, char_end=14)
    unknown = registry.resolve_mention(
        unknown,
        query="Ask Qzzyxx-999",
        requested_relations=("definition",),
    )

    assert exact.selected_entity_id == "entity:ada"
    assert exact.resolution_method.value == "exact_title"
    assert redirect.selected_entity_id == "entity:ada"
    assert redirect.resolution_method.value == "redirect"
    assert unknown.copy_status == "unknown_but_copyable"
    assert registry.verify_unknown_copy("Ask Qzzyxx-999", unknown)


def test_ambiguous_name_is_not_silently_selected() -> None:
    linked = _registry().resolve_mention(
        EntityMention(surface="Mercury", char_start=0, char_end=7),
        query="Mercury",
        requested_relations=(),
    )
    assert linked.copy_status == "ambiguous"
    assert linked.selected_entity_id is None
    assert len(linked.candidates) == 2


def test_frame_linking_preserves_unknown_surface_offsets() -> None:
    query = "What is Qzzyxx?"
    frame = _registry().link_frame(QueryFramer().frame(query))
    unknown = next(item for item in frame.entity_mentions if item.surface == "Qzzyxx")
    assert unknown.copy_status == "unknown_but_copyable"
    assert query[unknown.char_start : unknown.char_end] == "Qzzyxx"


def test_natural_benchmark_phrasings_map_to_broad_shapes() -> None:
    framer = QueryFramer()

    assert framer.frame("Which date is stated about Ada?").answer_shape is AnswerShape.DATE
    assert (
        framer.frame("Which quantity is stated about Ada?").answer_shape
        is AnswerShape.QUANTITY
    )
    assert (
        framer.frame("What does the analytical engine refer to?").answer_shape
        is AnswerShape.DEFINITION
    )
    assert (
        framer.frame("And how does that source define Ada?").answer_shape
        is AnswerShape.DEFINITION
    )
    assert (
        framer.frame("Which source states the larger km value, Ada or Babbage?").answer_shape
        is AnswerShape.COMPARISON
    )
    assert (
        framer.frame("Using both sources, what are Ada and Babbage?").answer_shape
        is AnswerShape.LIST
    )
    assert framer.frame("Ada—what about it?").clarification_need
