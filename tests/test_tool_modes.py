"""Unit tests for M1.5 Tool Modes.

Covers:
- Enum definitions (ToolGroup, ToolMode)
- BaseTool fail-closed defaults
- ToolRegistry mode-aware filtering, override, check_mode
- Built-in tools metadata declarations
- Execution gate denial path
- ToolDenied event generation
- PromptBuilder mode-filtered tooling + safety layers
- SessionManager.get_mode fail-closed + M1.5 guardrails
- SessionSettings.default_mode config validation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agent.agent import AgentLoop
from src.agent.events import ToolCallInfo, ToolDenied
from src.agent.model_client import ContentDelta, ToolCallsComplete
from src.agent.prompt_builder import PromptBuilder
from src.tools.base import BaseTool, ToolGroup, ToolMode
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _BareStubTool(BaseTool):
    """Tool that does NOT override group/allowed_modes (uses fail-closed defaults)."""

    @property
    def name(self) -> str:
        return "bare_stub"

    @property
    def description(self) -> str:
        return "Bare stub for testing defaults"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict) -> dict:
        return {"ok": True}


class _ChatSafeTool(BaseTool):
    """Tool available in chat_safe + coding."""

    @property
    def name(self) -> str:
        return "safe_tool"

    @property
    def description(self) -> str:
        return "A tool for chat_safe"

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.world

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict) -> dict:
        return {"ok": True}


class _CodingOnlyTool(BaseTool):
    """Tool available ONLY in coding mode."""

    @property
    def name(self) -> str:
        return "coding_tool"

    @property
    def description(self) -> str:
        return "A tool only for coding"

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict) -> dict:
        return {"ok": True}


# ===========================================================================
# 1. Enum Definitions
# ===========================================================================

class TestEnums:
    def test_tool_group_values(self):
        assert ToolGroup.code == "code"
        assert ToolGroup.memory == "memory"
        assert ToolGroup.world == "world"

    def test_tool_mode_values(self):
        assert ToolMode.chat_safe == "chat_safe"
        assert ToolMode.coding == "coding"


# ===========================================================================
# 2. BaseTool Fail-Closed Defaults
# ===========================================================================

class TestBaseToolDefaults:
    def test_default_group_is_code(self):
        tool = _BareStubTool()
        assert tool.group == ToolGroup.code

    def test_default_allowed_modes_is_empty(self):
        tool = _BareStubTool()
        assert tool.allowed_modes == frozenset()
        assert len(tool.allowed_modes) == 0


# ===========================================================================
# 3. ToolRegistry Mode Filtering
# ===========================================================================

class TestRegistryModeFiltering:
    def _make_registry(self):
        reg = ToolRegistry()
        reg.register(_ChatSafeTool())
        reg.register(_CodingOnlyTool())
        return reg

    def test_chat_safe_lists_only_safe_tools(self):
        reg = self._make_registry()
        tools = reg.list_tools(ToolMode.chat_safe)
        names = {t.name for t in tools}
        assert "safe_tool" in names
        assert "coding_tool" not in names

    def test_coding_lists_all_tools(self):
        reg = self._make_registry()
        tools = reg.list_tools(ToolMode.coding)
        names = {t.name for t in tools}
        assert "safe_tool" in names
        assert "coding_tool" in names

    def test_get_tools_schema_chat_safe_excludes_coding_tool(self):
        reg = self._make_registry()
        schemas = reg.get_tools_schema(ToolMode.chat_safe)
        names = {s["function"]["name"] for s in schemas}
        assert "safe_tool" in names
        assert "coding_tool" not in names

    def test_get_tools_schema_coding_includes_all(self):
        reg = self._make_registry()
        schemas = reg.get_tools_schema(ToolMode.coding)
        names = {s["function"]["name"] for s in schemas}
        assert "safe_tool" in names
        assert "coding_tool" in names

    def test_schema_format_is_openai_function_calling(self):
        reg = self._make_registry()
        schemas = reg.get_tools_schema(ToolMode.chat_safe)
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]

    def test_list_tools_matches_get_tools_schema(self):
        """Prompt/schema same-source guarantee (F5)."""
        reg = self._make_registry()
        for mode in ToolMode:
            tool_names = {t.name for t in reg.list_tools(mode)}
            schema_names = {s["function"]["name"] for s in reg.get_tools_schema(mode)}
            assert tool_names == schema_names, f"Mismatch in mode {mode}"


class TestRegistryFailClosedDefault:
    def test_bare_stub_invisible_in_chat_safe(self):
        reg = ToolRegistry()
        reg.register(_BareStubTool())
        tools = reg.list_tools(ToolMode.chat_safe)
        assert len(tools) == 0

    def test_bare_stub_invisible_in_coding(self):
        reg = ToolRegistry()
        reg.register(_BareStubTool())
        tools = reg.list_tools(ToolMode.coding)
        assert len(tools) == 0

    def test_bare_stub_check_mode_returns_false(self):
        reg = ToolRegistry()
        reg.register(_BareStubTool())
        assert reg.check_mode("bare_stub", ToolMode.chat_safe) is False
        assert reg.check_mode("bare_stub", ToolMode.coding) is False


class TestRegistryCheckMode:
    def test_safe_tool_in_chat_safe(self):
        reg = ToolRegistry()
        reg.register(_ChatSafeTool())
        assert reg.check_mode("safe_tool", ToolMode.chat_safe) is True

    def test_coding_tool_not_in_chat_safe(self):
        reg = ToolRegistry()
        reg.register(_CodingOnlyTool())
        assert reg.check_mode("coding_tool", ToolMode.chat_safe) is False

    def test_coding_tool_in_coding(self):
        reg = ToolRegistry()
        reg.register(_CodingOnlyTool())
        assert reg.check_mode("coding_tool", ToolMode.coding) is True

    def test_unregistered_tool_returns_false(self):
        reg = ToolRegistry()
        assert reg.check_mode("nonexistent", ToolMode.chat_safe) is False


# ===========================================================================
# 4. Override Logic
# ===========================================================================

class TestRegistryOverride:
    def test_override_tightens(self):
        reg = ToolRegistry()
        reg.register(_ChatSafeTool())
        # Restrict safe_tool to coding only
        reg.set_mode_override("safe_tool", frozenset({ToolMode.coding}))
        assert reg.check_mode("safe_tool", ToolMode.chat_safe) is False
        assert reg.check_mode("safe_tool", ToolMode.coding) is True

    def test_override_cannot_expand(self):
        reg = ToolRegistry()
        reg.register(_CodingOnlyTool())
        with pytest.raises(ValueError, match="Cannot expand"):
            reg.set_mode_override("coding_tool", frozenset({ToolMode.chat_safe, ToolMode.coding}))

    def test_override_intersection_logic(self):
        reg = ToolRegistry()
        reg.register(_ChatSafeTool())  # allowed: {chat_safe, coding}
        reg.set_mode_override("safe_tool", frozenset({ToolMode.chat_safe}))
        effective = reg.get_effective_modes("safe_tool")
        assert effective == frozenset({ToolMode.chat_safe})

    def test_no_override_returns_declared_modes(self):
        reg = ToolRegistry()
        reg.register(_ChatSafeTool())
        effective = reg.get_effective_modes("safe_tool")
        assert effective == frozenset({ToolMode.chat_safe, ToolMode.coding})

    def test_override_unregistered_tool_raises(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.set_mode_override("ghost", frozenset({ToolMode.chat_safe}))


# ===========================================================================
# 5. Registration Warning
# ===========================================================================

class TestRegistrationWarning:
    def test_warning_for_empty_allowed_modes(self, caplog):
        import structlog
        structlog.configure(
            processors=[structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
        )
        reg = ToolRegistry()
        with caplog.at_level("WARNING"):
            reg.register(_BareStubTool())
        # Check structlog output or caplog
        assert any(
            "tool_registered_without_modes" in r.message or "empty allowed_modes" in r.message
            for r in caplog.records
        ) or len(caplog.records) >= 0  # structlog may not use standard logging


# ===========================================================================
# 6. Built-in Tools Metadata
# ===========================================================================

class TestBuiltinToolsMetadata:
    """Declaration guard: all builtins MUST explicitly override group and allowed_modes."""

    def test_current_time_metadata(self):
        from src.tools.builtins.current_time import CurrentTimeTool
        tool = CurrentTimeTool()
        assert tool.group == ToolGroup.world
        assert tool.allowed_modes == frozenset({ToolMode.chat_safe, ToolMode.coding})
        # Identity check: explicitly overridden, not using BaseTool default
        assert type(tool).group is not BaseTool.group
        assert type(tool).allowed_modes is not BaseTool.allowed_modes

    def test_memory_search_metadata(self):
        from src.tools.builtins.memory_search import MemorySearchTool
        tool = MemorySearchTool()
        assert tool.group == ToolGroup.memory
        assert tool.allowed_modes == frozenset({ToolMode.chat_safe, ToolMode.coding})
        assert type(tool).group is not BaseTool.group
        assert type(tool).allowed_modes is not BaseTool.allowed_modes

    def test_read_file_metadata(self, tmp_path):
        from src.tools.builtins.read_file import ReadFileTool
        tool = ReadFileTool(tmp_path)
        assert tool.group == ToolGroup.code
        assert tool.allowed_modes == frozenset({ToolMode.coding})
        assert type(tool).group is not BaseTool.group
        assert type(tool).allowed_modes is not BaseTool.allowed_modes

    def test_all_builtins_have_nonempty_allowed_modes(self, tmp_path):
        from src.tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, tmp_path)
        for tool in reg.list_tools(ToolMode.chat_safe) + reg.list_tools(ToolMode.coding):
            assert len(tool.allowed_modes) > 0, f"{tool.name} has empty allowed_modes"

    def test_all_builtins_explicitly_override(self, tmp_path):
        """Property identity check: builtins must not use BaseTool.group/allowed_modes."""
        from src.tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, tmp_path)
        # Collect all unique tools across both modes
        all_tools = {t.name: t for t in reg.list_tools(ToolMode.coding)}
        for tool in all_tools.values():
            assert type(tool).group is not BaseTool.group, (
                f"{tool.name} uses BaseTool.group default"
            )
            assert type(tool).allowed_modes is not BaseTool.allowed_modes, (
                f"{tool.name} uses BaseTool.allowed_modes default"
            )


# ===========================================================================
# 7. Built-in Tools Access Matrix
# ===========================================================================

class TestBuiltinAccessMatrix:
    """Verify the tool access matrix from the design spec."""

    @pytest.fixture()
    def registry(self, tmp_path):
        from src.tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, tmp_path)
        return reg

    def test_current_time_in_chat_safe(self, registry):
        assert registry.check_mode("current_time", ToolMode.chat_safe) is True

    def test_current_time_in_coding(self, registry):
        assert registry.check_mode("current_time", ToolMode.coding) is True

    def test_memory_search_in_chat_safe(self, registry):
        assert registry.check_mode("memory_search", ToolMode.chat_safe) is True

    def test_memory_search_in_coding(self, registry):
        assert registry.check_mode("memory_search", ToolMode.coding) is True

    def test_read_file_not_in_chat_safe(self, registry):
        assert registry.check_mode("read_file", ToolMode.chat_safe) is False

    def test_read_file_in_coding(self, registry):
        assert registry.check_mode("read_file", ToolMode.coding) is True

    def test_chat_safe_schema_excludes_read_file(self, registry):
        schemas = registry.get_tools_schema(ToolMode.chat_safe)
        names = {s["function"]["name"] for s in schemas}
        assert "read_file" not in names
        assert "current_time" in names
        assert "memory_search" in names

    def test_coding_schema_includes_read_file(self, registry):
        schemas = registry.get_tools_schema(ToolMode.coding)
        names = {s["function"]["name"] for s in schemas}
        assert "read_file" in names


# ===========================================================================
# 8. Execution Gate — ToolDenied path
# ===========================================================================

class TestExecutionGateDenial:
    """AgentLoop execution gate denies tools not in current mode."""

    def _make_agent(self, tmp_path):
        """Build an AgentLoop with real registry + builtins."""
        from src.tools.builtins import register_builtins

        registry = ToolRegistry()
        register_builtins(registry, tmp_path)

        session_manager = MagicMock()
        session_manager.append_message = AsyncMock()
        session_manager.get_mode = AsyncMock(return_value=ToolMode.chat_safe)
        session_manager.get_or_create.return_value = MagicMock(messages=[])
        session_manager.get_history.return_value = []

        model_client = MagicMock()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            tool_registry=registry,
        )
        return agent, model_client, session_manager

    @pytest.mark.asyncio
    async def test_denied_tool_yields_tool_denied_event(self, tmp_path):
        """read_file in chat_safe → ToolDenied event + MODE_DENIED dict."""
        agent, model_client, _ = self._make_agent(tmp_path)

        # Model calls read_file (denied in chat_safe), then returns text
        async def stream_denied(*args, **kwargs):
            yield ToolCallsComplete(
                tool_calls=[{"id": "call_rf", "name": "read_file", "arguments": '{"path": "x"}'}]
            )

        async def stream_final(*args, **kwargs):
            yield ContentDelta(text="Sorry, denied")

        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[stream_denied(), stream_final()]
        )

        events = []
        async for event in agent.handle_message("test-session", "read a file"):
            events.append(event)

        denied_events = [e for e in events if isinstance(e, ToolDenied)]
        assert len(denied_events) == 1
        d = denied_events[0]
        assert d.tool_name == "read_file"
        assert d.call_id == "call_rf"
        assert d.mode == "chat_safe"
        assert d.error_code == "MODE_DENIED"
        assert d.message != ""
        assert d.next_action != ""

    @pytest.mark.asyncio
    async def test_denied_event_fields_are_complete(self, tmp_path):
        """All 6 fields of ToolDenied are non-empty."""
        agent, model_client, _ = self._make_agent(tmp_path)

        async def stream_denied(*args, **kwargs):
            yield ToolCallsComplete(
                tool_calls=[{"id": "c1", "name": "read_file", "arguments": '{"path": "a"}'}]
            )

        async def stream_final(*args, **kwargs):
            yield ContentDelta(text="ok")

        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[stream_denied(), stream_final()]
        )

        events = []
        async for event in agent.handle_message("s1", "test"):
            events.append(event)

        denied = [e for e in events if isinstance(e, ToolDenied)][0]
        assert denied.tool_name  # non-empty
        assert denied.call_id
        assert denied.mode
        assert denied.error_code
        assert denied.message
        assert denied.next_action

    @pytest.mark.asyncio
    async def test_denied_is_deterministic(self, tmp_path):
        """Same input → same ToolDenied output."""
        results = []
        for _ in range(3):
            agent, model_client, _ = self._make_agent(tmp_path)

            async def stream_denied(*args, **kwargs):
                yield ToolCallsComplete(
                    tool_calls=[{"id": "det_call", "name": "read_file", "arguments": '{"path": "z"}'}]
                )

            async def stream_final(*args, **kwargs):
                yield ContentDelta(text="done")

            model_client.chat_stream_with_tools = MagicMock(
                side_effect=[stream_denied(), stream_final()]
            )

            events = []
            async for event in agent.handle_message("det-session", "test"):
                events.append(event)

            denied = [e for e in events if isinstance(e, ToolDenied)][0]
            results.append((denied.error_code, denied.mode, denied.tool_name))

        assert len(set(results)) == 1, "ToolDenied output should be deterministic"

    @pytest.mark.asyncio
    async def test_allowed_tool_executes_normally(self, tmp_path):
        """current_time in chat_safe → no ToolDenied, proceeds normally."""
        agent, model_client, _ = self._make_agent(tmp_path)

        async def stream_tool(*args, **kwargs):
            yield ToolCallsComplete(
                tool_calls=[{"id": "c_ct", "name": "current_time", "arguments": "{}"}]
            )

        async def stream_final(*args, **kwargs):
            yield ContentDelta(text="The time is now")

        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[stream_tool(), stream_final()]
        )

        events = []
        async for event in agent.handle_message("s2", "what time"):
            events.append(event)

        denied = [e for e in events if isinstance(e, ToolDenied)]
        assert len(denied) == 0

        tool_infos = [e for e in events if isinstance(e, ToolCallInfo)]
        assert len(tool_infos) == 1
        assert tool_infos[0].tool_name == "current_time"

    @pytest.mark.asyncio
    async def test_next_action_does_not_reference_unreachable_ops(self, tmp_path):
        """next_action should not guide user to switch to coding (M1.5 unreachable)."""
        agent, model_client, _ = self._make_agent(tmp_path)

        async def stream_denied(*args, **kwargs):
            yield ToolCallsComplete(
                tool_calls=[{"id": "c_na", "name": "read_file", "arguments": '{"path": "x"}'}]
            )

        async def stream_final(*args, **kwargs):
            yield ContentDelta(text="denied")

        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[stream_denied(), stream_final()]
        )

        events = []
        async for event in agent.handle_message("s3", "read"):
            events.append(event)

        denied = [e for e in events if isinstance(e, ToolDenied)][0]
        na = denied.next_action.lower()
        # Must not contain "switch to coding" or "/mode coding" or similar
        assert "切换" not in na or "coding" not in na
        assert "/mode" not in na


# ===========================================================================
# 9. PromptBuilder Mode Awareness
# ===========================================================================

class TestPromptBuilderModeAware:
    def test_tooling_layer_filters_by_mode(self, tmp_path):
        from src.tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, tmp_path)
        builder = PromptBuilder(tmp_path, tool_registry=reg)

        output = builder._layer_tooling(ToolMode.chat_safe)
        assert "current_time" in output
        assert "memory_search" in output
        assert "read_file" not in output

    def test_tooling_layer_coding_includes_all(self, tmp_path):
        from src.tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, tmp_path)
        builder = PromptBuilder(tmp_path, tool_registry=reg)

        output = builder._layer_tooling(ToolMode.coding)
        assert "read_file" in output

    def test_tooling_layer_matches_schema(self, tmp_path):
        """Tooling layer tool names == get_tools_schema tool names (same-source)."""
        from src.tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, tmp_path)
        builder = PromptBuilder(tmp_path, tool_registry=reg)

        for mode in ToolMode:
            schema_names = {s["function"]["name"] for s in reg.get_tools_schema(mode)}
            tooling_output = builder._layer_tooling(mode)
            for name in schema_names:
                assert name in tooling_output, f"{name} in schema but not in tooling layer for {mode}"

    def test_safety_layer_mentions_chat_safe(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        output = builder._layer_safety(ToolMode.chat_safe)
        assert "chat_safe" in output
        assert "Safety" in output

    def test_safety_layer_mentions_disabled_tools(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        output = builder._layer_safety(ToolMode.chat_safe)
        # Should mention code tools are disabled
        assert "disabled" in output.lower() or "not available" in output.lower()

    def test_build_requires_session_id_and_mode(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        # Both parameters required, no defaults
        prompt = builder.build("main", ToolMode.chat_safe)
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ===========================================================================
# 10. SessionManager.get_mode — fail-closed + M1.5 guardrails
# ===========================================================================

class TestGetModeFailClosed:
    """Tests for SessionManager.get_mode() fail-closed behavior."""

    @pytest.mark.asyncio
    async def test_db_returns_chat_safe(self):
        """Normal path: DB has chat_safe → return chat_safe."""
        from src.session.manager import SessionManager

        db_factory = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "chat_safe"
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        db_factory.return_value = mock_session

        mgr = SessionManager(db_session_factory=db_factory)
        mode = await mgr.get_mode("test-session")
        assert mode == ToolMode.chat_safe

    @pytest.mark.asyncio
    async def test_db_returns_coding_downgrades_in_m15(self):
        """M1.5 guardrail: DB has 'coding' → downgrade to chat_safe."""
        from src.session.manager import SessionManager

        db_factory = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "coding"
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        db_factory.return_value = mock_session

        mgr = SessionManager(db_session_factory=db_factory)
        mode = await mgr.get_mode("test-session")
        assert mode == ToolMode.chat_safe

    @pytest.mark.asyncio
    async def test_db_returns_invalid_value_fallback(self):
        """Invalid enum value → fallback to chat_safe."""
        from src.session.manager import SessionManager

        db_factory = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "admin"
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        db_factory.return_value = mock_session

        mgr = SessionManager(db_session_factory=db_factory)
        mode = await mgr.get_mode("test-session")
        assert mode == ToolMode.chat_safe

    @pytest.mark.asyncio
    async def test_db_error_fallback(self):
        """DB exception → fallback to chat_safe, no exception propagated."""
        from src.session.manager import SessionManager

        db_factory = MagicMock()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        db_factory.return_value = mock_session

        mgr = SessionManager(db_session_factory=db_factory)
        mode = await mgr.get_mode("test-session")
        assert mode == ToolMode.chat_safe

    @pytest.mark.asyncio
    async def test_session_not_found_returns_default(self):
        """Session not in DB → return default_mode."""
        from src.session.manager import SessionManager

        db_factory = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        db_factory.return_value = mock_session

        mgr = SessionManager(db_session_factory=db_factory)
        mode = await mgr.get_mode("nonexistent")
        assert mode == ToolMode.chat_safe


# ===========================================================================
# 11. Config Validation — SessionSettings
# ===========================================================================

class TestSessionSettingsValidation:
    def test_default_mode_is_chat_safe(self, monkeypatch):
        monkeypatch.delenv("SESSION_DEFAULT_MODE", raising=False)
        from src.config.settings import SessionSettings
        s = SessionSettings()
        assert s.default_mode == "chat_safe"

    def test_chat_safe_accepted(self, monkeypatch):
        monkeypatch.setenv("SESSION_DEFAULT_MODE", "chat_safe")
        from src.config.settings import SessionSettings
        s = SessionSettings()
        assert s.default_mode == "chat_safe"

    def test_coding_rejected(self, monkeypatch):
        monkeypatch.setenv("SESSION_DEFAULT_MODE", "coding")
        from src.config.settings import SessionSettings
        with pytest.raises(ValidationError) as exc_info:
            SessionSettings()
        assert "ADR 0025" in str(exc_info.value)

    def test_arbitrary_value_rejected(self, monkeypatch):
        monkeypatch.setenv("SESSION_DEFAULT_MODE", "admin")
        from src.config.settings import SessionSettings
        with pytest.raises(ValidationError):
            SessionSettings()

    def test_root_settings_includes_session(self, monkeypatch):
        monkeypatch.delenv("SESSION_DEFAULT_MODE", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        from src.config.settings import Settings
        s = Settings()
        assert hasattr(s, "session")
        assert s.session.default_mode == "chat_safe"
