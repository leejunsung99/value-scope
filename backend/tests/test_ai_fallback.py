"""AI_PROVIDER=claude_cli: Claude CLI first, Codex CLI when Claude fails."""
import pytest

from app.services import ai


class Stub(ai.AIProvider):
    def __init__(self, name, data=None, error=None):
        self.name, self.model, self.data, self.error, self.calls = name, "m", data, error, 0

    def complete_json(self, system, prompt, schema):
        self.calls += 1
        if self.error:
            raise ai.AIError(self.error)
        return ai.AIResult(self.data, self.name, self.model)


def test_codex_answers_only_when_claude_fails():
    claude, codex = Stub("claude_cli", {"by": "claude"}), Stub("codex_cli", {"by": "codex"})
    chain = ai.FallbackProvider([claude, codex])
    assert chain.complete_json("s", "p", {}).data == {"by": "claude"} and codex.calls == 0
    claude.error = "usage limit"
    r = chain.complete_json("s", "p", {})
    assert r.data == {"by": "codex"} and r.provider == "codex_cli"
    codex.error = "logged out"
    with pytest.raises(ai.AIError, match="usage limit.*logged out"):
        chain.complete_json("s", "p", {})


def test_get_provider_builds_claude_then_codex(monkeypatch):
    from app.config import Settings
    monkeypatch.setattr(ai.shutil, "which", lambda name: f"C:/npm/{name}.CMD")
    p = ai.get_provider(Settings(AI_PROVIDER="claude_cli"))
    assert isinstance(p, ai.FallbackProvider)
    assert [x.name for x in p.providers] == ["claude_cli", "codex_cli"]
    monkeypatch.setattr(ai.shutil, "which", lambda name: "C:/npm/codex.CMD" if name == "codex" else None)
    assert isinstance(ai.get_provider(Settings(AI_PROVIDER="claude_cli")), ai.CodexCLIProvider)
    monkeypatch.setattr(ai.shutil, "which", lambda name: None)
    assert isinstance(ai.get_provider(Settings(AI_PROVIDER="claude_cli")), ai.NoAIProvider)


def test_codex_command_is_read_only_and_isolated(monkeypatch):
    monkeypatch.setattr(ai.shutil, "which", lambda name: "C:/npm/codex.CMD")
    cmd = ai.CodexCLIProvider("gpt-x").build_command("C:/tmp/x", "C:/tmp/x/a.txt")
    assert cmd[:2] == ["C:/npm/codex.CMD", "exec"] and cmd[cmd.index("-s") + 1] == "read-only"
    assert cmd[cmd.index("-C") + 1] == "C:/tmp/x" and "--ephemeral" in cmd and cmd[-1] == "-"
