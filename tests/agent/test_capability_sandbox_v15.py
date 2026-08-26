from __future__ import annotations

from aethersparse.agent.capabilities import accessory_p4_capability_model, host_capability_model
from aethersparse.agent.tools import SandboxedToolExecutor, ToolKind, ToolRequest


def test_self_model_changes_with_hardware_and_p4_rejects_host_tools(tmp_path) -> None:
    host = host_capability_model("tree:host")
    p4 = accessory_p4_capability_model("tree:p4")
    assert host.hardware_class != p4.hardware_class
    assert ToolKind.BUILD in host.available_tools
    assert ToolKind.BUILD not in p4.available_tools

    executor = SandboxedToolExecutor(
        tmp_path / "sandboxes", available_tools=frozenset(p4.available_tools)
    )
    result = executor.execute(
        ToolRequest(
            request_id="create",
            kind=ToolKind.CREATE_SANDBOX,
            arguments={"name": "unsupported"},
        )
    )
    assert not result.success
    assert "unavailable" in result.output
