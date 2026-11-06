"""LLM provider adapter.

Numerical work is always done in Python. The LLM is only used for interpretation of
official documents (e.g. classifying related companies mentioned in a 사업보고서) and,
in later phases, for report drafting and review. Every provider returns JSON that is
validated against a schema by the caller.

Providers
- anthropic : Claude via the official Anthropic SDK (AI_API_KEY). Recommended for unattended use.
- claude_cli: the locally installed `claude` CLI in headless mode (`claude -p`). Uses the
              CLI's own login; check that your plan's terms allow this use.
- none      : no LLM. Callers fall back to rule-based behaviour and say so.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from app.config import Settings


class AIUnavailable(Exception):
    pass


class AIError(Exception):
    pass


@dataclass
class AIResult:
    data: dict
    provider: str
    model: str


class AIProvider:
    name = "base"
    model = ""

    def complete_json(self, system: str, prompt: str, schema: dict) -> AIResult:  # pragma: no cover
        raise NotImplementedError


class NoAIProvider(AIProvider):
    name = "none"

    def __init__(self, reason: str = "AI_PROVIDER=none — AI 분석이 비활성화되어 있습니다."):
        self.reason = reason

    def complete_json(self, system: str, prompt: str, schema: dict) -> AIResult:
        raise AIUnavailable(self.reason)


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise AIUnavailable("AI_API_KEY가 설정되지 않았습니다.")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete_json(self, system: str, prompt: str, schema: dict) -> AIResult:
        a = self._anthropic
        try:
            # Server-side refusal fallback is enabled so a policy decline is retried on a
            # fallback model instead of silently failing.
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as e:
            raise AIError("Anthropic API 인증 실패 — AI_API_KEY를 확인하세요.") from e
        except a.RateLimitError as e:
            raise AIError("Anthropic API 요청 한도 초과 — 잠시 후 다시 시도하세요.") from e
        except a.APIStatusError as e:
            raise AIError(f"Anthropic API 오류 (HTTP {e.status_code})") from e
        except a.APIConnectionError as e:
            raise AIError("Anthropic API 네트워크 연결 실패") from e
        if resp.stop_reason == "refusal":
            raise AIError("AI 모델이 요청 처리를 거절했습니다.")
        if resp.stop_reason == "max_tokens":
            raise AIError("AI 응답이 길이 제한으로 잘렸습니다.")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise AIError("AI 응답 JSON 파싱 실패") from e
        return AIResult(data=data, provider=self.name, model=resp.model)


class ClaudeCLIProvider(AIProvider):
    """Headless `claude -p` call, kept lean: no tools, no MCP servers, our own system
    prompt, no session persistence, and a neutral working directory so no project
    CLAUDE.md is loaded. Uses the CLI's own login (OAuth) — no API key in this app."""

    name = "claude_cli"

    def __init__(self, model: str = "opus", timeout: int = 900):
        exe = shutil.which("claude")
        if not exe:
            raise AIUnavailable("claude CLI를 찾을 수 없습니다. Claude Code 설치 및 로그인(`claude`)을 확인하세요.")
        self.exe = exe
        self.model = model or "opus"
        self.timeout = timeout

    def build_command(self, system: str) -> list[str]:
        return [
            self.exe, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt", system,
        ]

    def complete_json(self, system: str, prompt: str, schema: dict) -> AIResult:
        full = (
            f"{prompt}\n\n"
            "Respond with ONLY a JSON object (no markdown fences) matching this JSON schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="valuescope-ai-") as cwd:
                proc = subprocess.run(
                    self.build_command(system),
                    input=full,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.timeout,
                    cwd=cwd,
                )
        except subprocess.TimeoutExpired as e:
            raise AIError("claude CLI 응답 시간 초과") from e
        try:
            outer = json.loads(proc.stdout)
        except json.JSONDecodeError:
            outer = None
        if proc.returncode != 0 or not isinstance(outer, dict) or outer.get("is_error"):
            detail = (outer or {}).get("result") if isinstance(outer, dict) else (proc.stdout or proc.stderr)
            detail = str(detail or "").strip()[:200]
            if "unrecognized_model" in detail:
                detail = f"CLI가 모델 '{self.model}'을 인식하지 못했습니다. CLAUDE_CLI_MODEL을 opus/sonnet 등으로 설정하세요."
            raise AIError(f"claude CLI 실행 실패 (exit {proc.returncode}): {detail}")
        text = str(outer.get("result", "")).strip()
        try:
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except (json.JSONDecodeError, ValueError) as e:
            raise AIError("claude CLI 응답 JSON 파싱 실패") from e
        used = [m for m in (outer.get("modelUsage") or {}) if "haiku" not in m] or [self.model]
        return AIResult(data=data, provider=self.name, model=", ".join(used))


def get_provider(settings: Settings) -> AIProvider:
    p = (settings.ai_provider or "none").lower()
    try:
        if p == "anthropic":
            return AnthropicProvider(settings.ai_api_key, settings.ai_model)
        if p == "claude_cli":
            return ClaudeCLIProvider(settings.claude_cli_model)
    except AIUnavailable as e:
        return NoAIProvider(f"AI_PROVIDER={p} 사용 불가: {e}")
    return NoAIProvider()
