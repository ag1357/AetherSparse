"""Five small, generic, real-execution qualifications for the typed tool plane."""

from __future__ import annotations

import difflib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aethersparse.agent.tools import (
    SandboxedToolExecutor,
    ToolArgument,
    ToolKind,
    ToolRequest,
    ToolResult,
)


@dataclass(frozen=True)
class _Task:
    task_id: str
    category: str
    before: str
    after: str
    test: str
    search: str


_TASKS = (
    _Task(
        "fix-unit-test",
        "fix_failing_unit_test",
        "def clamp(value, low, high):\n    return value\n",
        "def clamp(value, low, high):\n    return max(low, min(high, value))\n",
        "from module import clamp\nassert clamp(12, 0, 10) == 10\n",
        "return value",
    ),
    _Task(
        "small-feature",
        "add_deterministic_feature",
        "def parity(value):\n    return 'unknown'\n",
        "def parity(value):\n    return 'even' if value % 2 == 0 else 'odd'\n",
        "from module import parity\nassert parity(4) == 'even'\nassert parity(3) == 'odd'\n",
        "unknown",
    ),
    _Task(
        "parser-repair",
        "modify_parser",
        "def parse_field(text):\n    return tuple(text.split(':'))\n",
        "def parse_field(text):\n    return tuple(text.split(':', 1))\n",
        "from module import parse_field\nassert parse_field('url:https://a') == ('url', 'https://a')\n",
        "split",
    ),
    _Task(
        "api-field",
        "add_api_field",
        "def as_payload(name):\n    return {'name': name}\n",
        "def as_payload(name):\n    return {'name': name, 'schema_version': 1}\n",
        (
            "from module import as_payload\n"
            "assert as_payload('x') == {'name': 'x', 'schema_version': 1}\n"
        ),
        "as_payload",
    ),
    _Task(
        "compile-repair",
        "repair_compilation_defect",
        "def increment(value)\n    return value + 1\n",
        "def increment(value):\n    return value + 1\n",
        "from module import increment\nassert increment(2) == 3\n",
        "increment",
    ),
)


def _patch(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/module.py",
            tofile="b/module.py",
        )
    )


def _request(
    executor: SandboxedToolExecutor,
    task: _Task,
    index: int,
    kind: ToolKind,
    workspace: Path,
    **arguments: str,
) -> ToolResult:
    typed_arguments: dict[str, ToolArgument] = dict(arguments)
    return executor.execute(
        ToolRequest(
            request_id=f"{task.task_id}-{index}",
            kind=kind,
            workspace=str(workspace),
            arguments=typed_arguments,
        )
    )


def qualify_tool_plane(root: Path) -> dict[str, object]:
    """Run a deterministic inspect/repair/build/test/report loop on five repos."""

    root.mkdir(parents=True, exist_ok=True)
    outcomes: list[dict[str, object]] = []
    operation_total = 0
    for task in _TASKS:
        executor = SandboxedToolExecutor(
            root,
            command_allowlist={
                "fixture-build": (sys.executable, "-m", "py_compile", "module.py"),
                "fixture-tests": (sys.executable, "test_task.py"),
            },
        )
        created = executor.execute(
            ToolRequest(
                request_id=f"{task.task_id}-create",
                kind=ToolKind.CREATE_SANDBOX,
                arguments={"name": task.task_id},
            )
        )
        workspace = Path(created.output)
        (workspace / "module.py").write_text(task.before, encoding="utf-8")
        (workspace / "test_task.py").write_text(task.test, encoding="utf-8")
        subprocess.run(("git", "init", "-q"), cwd=workspace, check=True)

        results = [created]
        results.append(_request(executor, task, 1, ToolKind.LIST_TREE, workspace))
        initial_test = _request(
            executor, task, 2, ToolKind.RUN_TESTS, workspace, profile="fixture-tests"
        )
        results.append(initial_test)
        results.append(_request(executor, task, 3, ToolKind.INSPECT_FAILURE, workspace))
        results.append(
            _request(
                executor,
                task,
                4,
                ToolKind.SEARCH_SOURCE,
                workspace,
                query=task.search,
            )
        )
        results.append(_request(executor, task, 5, ToolKind.READ_FILE, workspace, path="module.py"))
        results.append(
            _request(
                executor,
                task,
                6,
                ToolKind.WRITE_PATCH,
                workspace,
                name="repair",
                patch=_patch(task.before, task.after),
            )
        )
        results.append(
            _request(
                executor,
                task,
                7,
                ToolKind.APPLY_PATCH,
                workspace,
                path=".aether-patches/repair.patch",
            )
        )
        build = _request(executor, task, 8, ToolKind.BUILD, workspace, profile="fixture-build")
        final_test = _request(
            executor, task, 9, ToolKind.RUN_TESTS, workspace, profile="fixture-tests"
        )
        results.extend((build, final_test))
        results.append(
            _request(
                executor,
                task,
                10,
                ToolKind.REPORT_RESULT,
                workspace,
                summary="bounded repair complete",
            )
        )
        operation_total += len(results)
        successful = (
            not initial_test.success
            and all(result.success for result in results if result is not initial_test)
            and build.success
            and final_test.success
        )
        outcomes.append(
            {
                "task_id": task.task_id,
                "category": task.category,
                "success": successful,
                "initial_failure_observed": not initial_test.success,
                "build_passed": build.success,
                "tests_passed": final_test.success,
                "operation_count": len(results),
                "invalid_action_attempts": 0,
            }
        )
    passed = sum(bool(item["success"]) for item in outcomes)
    return {
        "schema": "aethercore.v13.tool-plane-qualification.v1",
        "task_count": len(outcomes),
        "passed": passed,
        "success_rate": passed / len(outcomes),
        "operations": operation_total,
        "invalid_action_attempts": 0,
        "integration_performed": False,
        "tasks": outcomes,
    }


def write_qualification_report(root: Path, destination: Path) -> None:
    result = qualify_tool_plane(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
