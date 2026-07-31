from aethersparse.cells.smoke import canonical_smoke_bytes, cognitive_cell_smoke_report


def test_cognitive_cell_smoke_is_byte_deterministic() -> None:
    assert canonical_smoke_bytes() == canonical_smoke_bytes()


def test_cognitive_cell_smoke_preserves_external_accessory_boundary() -> None:
    report = cognitive_cell_smoke_report()
    assert report["scope"] == "CONTRACT_SMOKE_ONLY_NOT_REAL_CORPUS_EVIDENCE"
    assert report["terminal_role"] == "EXTERNAL_API_CLIENT_ONLY"
    assert report["decision"] == "NOT_QUALIFIED_WITHOUT_FROZEN_REAL_CORPUS_RUN"
    assert str(report["hybrid_pack_root"]).startswith("sha256:")
