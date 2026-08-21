from __future__ import annotations

from pathlib import Path

from aethersparse.agent.qualification import qualify_tool_plane
from aethersparse.agent.tools import SandboxedToolExecutor, ToolKind, ToolRequest


def test_five_generic_development_tasks_execute_real_repairs(tmp_path: Path) -> None:
    result = qualify_tool_plane(tmp_path / "qualification")
    assert result["passed"] == 5
    assert result["success_rate"] == 1.0
    assert result["invalid_action_attempts"] == 0
    assert result["integration_performed"] is False
    assert {item["category"] for item in result["tasks"]} == {
        "fix_failing_unit_test",
        "add_deterministic_feature",
        "modify_parser",
        "add_api_field",
        "repair_compilation_defect",
    }


def test_executor_blocks_escape_and_requires_one_time_integration_authorization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandboxes"
    executor = SandboxedToolExecutor(root)
    created = executor.execute(
        ToolRequest(request_id="create", kind=ToolKind.CREATE_SANDBOX, arguments={"name": "safe"})
    )
    workspace = created.output
    escaped = executor.execute(
        ToolRequest(
            request_id="escape",
            kind=ToolKind.READ_FILE,
            workspace=workspace,
            arguments={"path": "../outside"},
        )
    )
    assert escaped.success is False

    request = ToolRequest(
        request_id="integrate",
        kind=ToolKind.REQUEST_INTEGRATION,
        workspace=workspace,
        arguments={"authorization_id": "user-approved-7"},
    )
    assert executor.execute(request).success is False
    executor.authorize_integration("user-approved-7")
    authorized = executor.execute(request)
    assert authorized.success is True
    assert authorized.integration_performed is False
    assert executor.execute(request).success is False
