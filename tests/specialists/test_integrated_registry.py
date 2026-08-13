from pathlib import Path

from aethersparse.observer.registry import load_registry


def test_integrated_registry_is_sealed_and_keeps_unqualified_modules_inactive() -> None:
    registry = load_registry(
        Path("config/architecture/aethercore-v11-integrated.registry.json")
    )
    modules = {module.module_id: module for module in registry.modules}
    assert modules["aethercore.value-exact-scan"].status == "active"
    assert modules["aethercore.value-exact-scan"].parameter_count == 0
    assert modules["aethercore.entity-linear-baseline"].status == "inactive"
    assert modules["aethercore.probabilistic-fusion"].status == "inactive"
    assert modules["aethercore.adaptive-depth"].status == "inactive"
    assert modules["aethercore.research-observer"].status == "training_only"
