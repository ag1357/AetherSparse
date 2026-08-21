"""Typed, sandbox-first software-agent tools with no implicit integration path."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ToolKind(StrEnum):
    SEARCH_KNOWLEDGE = "SEARCH_KNOWLEDGE"
    SEARCH_SOURCE = "SEARCH_SOURCE"
    READ_FILE = "READ_FILE"
    LIST_TREE = "LIST_TREE"
    CREATE_SANDBOX = "CREATE_SANDBOX"
    CREATE_BRANCH_OR_WORKTREE = "CREATE_BRANCH_OR_WORKTREE"
    WRITE_PATCH = "WRITE_PATCH"
    APPLY_PATCH = "APPLY_PATCH"
    BUILD = "BUILD"
    RUN_TESTS = "RUN_TESTS"
    INSPECT_FAILURE = "INSPECT_FAILURE"
    REVERT = "REVERT"
    REPORT_RESULT = "REPORT_RESULT"
    REQUEST_INTEGRATION = "REQUEST_INTEGRATION"


ToolArgument = str | int | bool | list[str]


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    kind: ToolKind
    workspace: str | None = None
    arguments: dict[str, ToolArgument] = Field(default_factory=dict)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    kind: ToolKind
    success: bool
    summary: str
    output: str = ""
    changed_paths: tuple[str, ...] = ()
    integration_performed: bool = False


class KnowledgeSearch(Protocol):
    def search(self, query: str, limit: int) -> Sequence[str]: ...


class ToolSafetyError(ValueError):
    pass


class SandboxedToolExecutor:
    """Executes typed operations only beneath registered roots and without a shell."""

    def __init__(
        self,
        sandbox_root: Path,
        *,
        repository_roots: Sequence[Path] = (),
        command_allowlist: Mapping[str, Sequence[str]] | None = None,
        knowledge: KnowledgeSearch | None = None,
    ) -> None:
        self.sandbox_root = sandbox_root.resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self.repository_roots = tuple(path.resolve() for path in repository_roots)
        self.command_allowlist = {
            name: tuple(command) for name, command in (command_allowlist or {}).items()
        }
        self.knowledge = knowledge
        self._last_result: ToolResult | None = None
        self._integration_authorizations: set[str] = set()

    def authorize_integration(self, authorization_id: str) -> None:
        """Record explicit user authorization; this still never merges automatically."""

        if not authorization_id or len(authorization_id) > 128:
            raise ToolSafetyError("invalid integration authorization")
        self._integration_authorizations.add(authorization_id)

    @staticmethod
    def _safe_name(value: str, *, label: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value):
            raise ToolSafetyError(f"unsafe {label}")
        if ".." in Path(value).parts or value.startswith("/"):
            raise ToolSafetyError(f"unsafe {label}")
        return value

    @staticmethod
    def _within(root: Path, relative: str = ".") -> Path:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as error:
            raise ToolSafetyError("path escapes its registered root") from error
        return candidate

    def _workspace(self, request: ToolRequest) -> Path:
        if request.workspace is None:
            raise ToolSafetyError(f"{request.kind} requires a workspace")
        workspace = Path(request.workspace).resolve()
        self._within(self.sandbox_root, str(workspace.relative_to(self.sandbox_root)))
        if not workspace.is_dir():
            raise ToolSafetyError("workspace is not an existing sandbox")
        return workspace

    @staticmethod
    def _string(request: ToolRequest, name: str, *, required: bool = True) -> str:
        value = request.arguments.get(name)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value):
            raise ToolSafetyError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _integer(request: ToolRequest, name: str, default: int) -> int:
        value = request.arguments.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolSafetyError(f"{name} must be an integer")
        return value

    @staticmethod
    def _run(argv: Sequence[str], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
        completed = subprocess.run(
            tuple(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (completed.stdout + completed.stderr)[-32_768:]
        return completed.returncode == 0, output

    def execute(self, request: ToolRequest) -> ToolResult:
        try:
            result = self._execute(request)
        except (OSError, subprocess.SubprocessError, ToolSafetyError, ValueError) as error:
            result = ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=False,
                summary=type(error).__name__,
                output=str(error),
            )
        self._last_result = result
        return result

    def _execute(self, request: ToolRequest) -> ToolResult:
        if request.kind is ToolKind.CREATE_SANDBOX:
            name = self._safe_name(self._string(request, "name"), label="sandbox name")
            destination = self._within(self.sandbox_root, name)
            destination.mkdir(parents=False, exist_ok=False)
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary="isolated sandbox created",
                output=str(destination),
                changed_paths=(str(destination),),
            )

        if request.kind is ToolKind.CREATE_BRANCH_OR_WORKTREE:
            repository = Path(self._string(request, "repository")).resolve()
            if repository not in self.repository_roots:
                raise ToolSafetyError("repository is not registered")
            name = self._safe_name(self._string(request, "name"), label="worktree name")
            branch = self._safe_name(self._string(request, "branch"), label="branch")
            if not branch.startswith("agent/"):
                raise ToolSafetyError("agent worktree branches require the agent/ prefix")
            base = self._safe_name(self._string(request, "base"), label="base revision")
            destination = self._within(self.sandbox_root, name)
            success, output = self._run(
                ("git", "worktree", "add", "-b", branch, str(destination), base), repository
            )
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=success,
                summary="isolated worktree created" if success else "worktree creation failed",
                output=output,
                changed_paths=(str(destination),) if success else (),
            )

        workspace = self._workspace(request)
        if request.kind is ToolKind.SEARCH_KNOWLEDGE:
            if self.knowledge is None:
                raise ToolSafetyError("knowledge provider is not configured")
            query = self._string(request, "query")
            limit = min(max(self._integer(request, "limit", 8), 1), 32)
            knowledge_matches = self.knowledge.search(query, limit)
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary=f"{len(knowledge_matches)} knowledge matches",
                output="\n".join(knowledge_matches),
            )
        if request.kind is ToolKind.SEARCH_SOURCE:
            needle = self._string(request, "query").casefold()
            source_matches: list[str] = []
            for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
                if ".git" in path.parts or path.stat().st_size > 1_000_000:
                    continue
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if needle in line.casefold():
                        source_matches.append(f"{path.relative_to(workspace)}:{line_number}:{line}")
                        if len(source_matches) == 64:
                            break
                if len(source_matches) == 64:
                    break
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary=f"{len(source_matches)} source matches",
                output="\n".join(source_matches),
            )
        if request.kind is ToolKind.READ_FILE:
            path = self._within(workspace, self._string(request, "path"))
            if not path.is_file() or path.stat().st_size > 65_536:
                raise ToolSafetyError("file is missing or exceeds the bounded read limit")
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary="file read",
                output=path.read_text(encoding="utf-8", errors="replace"),
            )
        if request.kind is ToolKind.LIST_TREE:
            relative = self._string(request, "path", required=False) or "."
            root = self._within(workspace, relative)
            paths = [
                str(path.relative_to(workspace)) + ("/" if path.is_dir() else "")
                for path in sorted(root.rglob("*"))
                if ".git" not in path.parts
            ][:256]
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary=f"{len(paths)} paths",
                output="\n".join(paths),
            )
        if request.kind is ToolKind.WRITE_PATCH:
            patch_name = self._safe_name(self._string(request, "name"), label="patch name")
            patch_text = self._string(request, "patch")
            patch_dir = self._within(workspace, ".aether-patches")
            patch_dir.mkdir(exist_ok=True)
            path = self._within(patch_dir, f"{patch_name}.patch")
            path.write_text(patch_text, encoding="utf-8")
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary="patch staged",
                output=str(path.relative_to(workspace)),
                changed_paths=(str(path.relative_to(workspace)),),
            )
        if request.kind in {ToolKind.APPLY_PATCH, ToolKind.REVERT}:
            path = self._within(workspace, self._string(request, "path"))
            if path.suffix != ".patch" or not path.is_file():
                raise ToolSafetyError("only a staged .patch file may be applied or reverted")
            reverse = ("-R",) if request.kind is ToolKind.REVERT else ()
            checked, check_output = self._run(
                ("git", "apply", *reverse, "--check", str(path)), workspace
            )
            if not checked:
                return ToolResult(
                    request_id=request.request_id,
                    kind=request.kind,
                    success=False,
                    summary="patch check failed",
                    output=check_output,
                )
            success, output = self._run(("git", "apply", *reverse, str(path)), workspace)
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=success,
                summary="patch applied" if success else "patch apply failed",
                output=output,
            )
        if request.kind in {ToolKind.BUILD, ToolKind.RUN_TESTS}:
            profile = self._string(request, "profile")
            command = self.command_allowlist.get(profile)
            if command is None:
                raise ToolSafetyError("command profile is not allowlisted")
            success, output = self._run(command, workspace, timeout=60)
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=success,
                summary=f"{profile} passed" if success else f"{profile} failed",
                output=output,
            )
        if request.kind is ToolKind.INSPECT_FAILURE:
            if self._last_result is None:
                raise ToolSafetyError("no prior tool result")
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary="previous result inspected",
                output=self._last_result.model_dump_json(),
            )
        if request.kind is ToolKind.REPORT_RESULT:
            summary = self._string(request, "summary")
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary="result reported",
                output=summary,
            )
        if request.kind is ToolKind.REQUEST_INTEGRATION:
            authorization = self._string(request, "authorization_id")
            if authorization not in self._integration_authorizations:
                raise ToolSafetyError("explicit user integration authorization is required")
            self._integration_authorizations.remove(authorization)
            return ToolResult(
                request_id=request.request_id,
                kind=request.kind,
                success=True,
                summary="integration request authorized; no merge was performed",
                integration_performed=False,
            )
        raise ToolSafetyError(f"unsupported tool: {request.kind}")
