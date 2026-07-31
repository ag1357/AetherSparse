from aethersparse.cells.vsa import atom, bind, bundle, permute, similarity


def test_binary_vsa_is_deterministic_and_binding_is_reversible() -> None:
    entity = atom("entity:apollo-11")
    relation = atom("relation:landed-on")
    bound = bind(entity, relation)
    assert bind(bound, relation) == entity
    assert atom("entity:apollo-11") == entity
    assert similarity(entity, entity) == 1.0
    assert permute(entity, 7) != entity
    assert bundle([entity, relation])
