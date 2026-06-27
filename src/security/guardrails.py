"""
Phase 4: Prompt Injection Guardrail Layer

This module implements a heuristic input validation layer that runs BEFORE
any user query reaches the Gemini LLM. It protects against:

1. Prompt Injection — attempts to override system instructions
2. Scope Creep — asking the agent to perform non-revenue tasks
3. Data Exfiltration — trying to dump DB contents via the LLM
4. System Introspection — asking the agent to reveal its own prompt

Defense-in-depth: This guardrail is Layer 1. The hardened system prompt
in agent.py is Layer 2. Semgrep CI is Layer 3.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# INJECTION PATTERN REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that signal a prompt injection / jailbreak attempt
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Classic instruction override — allow extra words between verb and target
    (r"ignore\s+.{0,30}(instructions?|prompts?|rules?|context|constraints?)", "instruction_override"),
    (r"forget\s+.{0,30}(everything|instructions?|rules?|context|constraints?)", "instruction_override"),
    (r"disregard\s+.{0,30}(instructions?|rules?|prompts?|context|constraints?)", "instruction_override"),
    (r"override\s+.{0,20}(instructions?|system\s+prompt|rules?|constraints?)", "instruction_override"),

    # Persona hijack
    (r"you\s+are\s+now\s+a\s+different", "persona_hijack"),
    (r"act\s+as\s+(if\s+you\s+are|a\s+different|an?\s+evil|an?\s+unrestricted)", "persona_hijack"),
    (r"pretend\s+(you\s+are|to\s+be)\s+.{0,30}(different|unrestricted|evil|another)", "persona_hijack"),
    (r"new\s+(persona|role|identity|instructions?)", "persona_hijack"),
    (r"take\s+on\s+a\s+new\s+persona", "persona_hijack"),

    # System prompt / instruction extraction — allow "me", "the", "your" etc. in middle
    (r"(show|print|reveal|repeat|tell|output|dump)\s+.{0,30}(system\s+prompt|initial\s+prompt|your\s+instructions?|your\s+rules|your\s+context|your\s+prompt)", "prompt_extraction"),
    (r"what\s+(are|is)\s+your\s+(system\s+prompt|instructions?|initial\s+prompt|rules?)", "prompt_extraction"),
    (r"repeat\s+(everything|all|the\s+above|your\s+prompt|your\s+instructions?)", "prompt_extraction"),

    # Database / credential exfiltration — allow "the", "all", "me" etc. in middle
    (r"(show|list|dump|print|output)\s+.{0,25}(database|db|records?|credentials?|password|api\s*key|secrets?|tokens?)", "data_exfiltration"),
    (r"select\s+\*?\s*from\s+\w+", "data_exfiltration"),
    (r"(drop|truncate)\s+(table|database|schema|index)\s+\w+", "destructive_sql"),
    (r"delete\s+from\s+\w+", "destructive_sql"),
    (r"alter\s+table\s+\w+", "destructive_sql"),
    (r"insert\s+into\s+\w+", "unauthorized_write"),
    (r"update\s+\w+\s+set\s+", "unauthorized_write"),

    # Code injection
    (r"exec(ute)?\s*\(", "code_injection"),
    (r"eval\s*\(", "code_injection"),
    (r"__import__\s*\(", "code_injection"),
    (r"subprocess|os\.system|os\.popen", "code_injection"),

    # Scope escalation
    (r"(write|generate|create)\s+(me\s+)?(a\s+)?(poem|song|story|essay|code\s+for\s+hack)", "scope_escalation"),
    (r"(hack|exploit)\s+.{0,30}(system|competitor|target|server|database)", "scope_escalation"),
    (r"(bypass|circumvent)\s+.{0,20}(security|guardrails?|filter|restrictions?|system)", "scope_escalation"),

    # Destructive OS commands
    (r"rm\s+-rf|del\s+/[sqf]", "destructive_command"),

    # Network exfiltration to external domains
    (r"curl\s+|wget\s+|http(s)?://(?!ai\.google|cloud\.google)", "network_exfiltration"),
]

# Compile all patterns once at module load (performance optimization)
_COMPILED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), category)
    for pattern, category in _INJECTION_PATTERNS
]


# ─────────────────────────────────────────────────────────────────────────────
# RESULT DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    """Result of an input validation check."""
    is_safe: bool
    blocked_category: str | None = None
    blocked_pattern: str | None = None
    sanitized_query: str | None = None
    warning_message: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# CORE VALIDATION FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def validate_query(query: str) -> GuardrailResult:
    """
    Validates a user query for prompt injection and scope violations.

    Args:
        query: The raw user input string.

    Returns:
        GuardrailResult with is_safe=True if the query is allowed,
        or is_safe=False with details about what was blocked.
    """
    if not query or not query.strip():
        return GuardrailResult(
            is_safe=False,
            blocked_category="empty_input",
            warning_message="Empty query rejected."
        )

    # Length guard — excessively long inputs can be used to hide injections
    if len(query) > 2000:
        logger.warning(f"[GUARDRAIL] Query too long: {len(query)} chars. Truncating for safety.")
        query = query[:2000] + "... [truncated by security guardrail]"

    # Scan all injection patterns
    for compiled_pattern, category in _COMPILED_PATTERNS:
        match = compiled_pattern.search(query)
        if match:
            logger.warning(
                f"[GUARDRAIL] 🚨 Blocked query — category={category}, "
                f"matched='{match.group(0)[:60]}', query_preview='{query[:80]}'"
            )
            return GuardrailResult(
                is_safe=False,
                blocked_category=category,
                blocked_pattern=match.group(0),
                warning_message=_build_warning(category)
            )

    # Query passed all checks
    return GuardrailResult(
        is_safe=True,
        sanitized_query=query.strip()
    )


def _build_warning(category: str) -> str:
    """Returns a user-friendly rejection message by category."""
    messages = {
        "instruction_override": (
            "⛔ I can't process that request — it appears to be trying to override my operating instructions."
        ),
        "persona_hijack": (
            "⛔ I'm the Revenue Recovery Agent and I operate within a fixed scope. "
            "I can't adopt a different persona or role."
        ),
        "prompt_extraction": (
            "⛔ I can't reveal my system instructions or internal configuration."
        ),
        "data_exfiltration": (
            "⛔ I can't execute raw database queries or expose stored credentials."
        ),
        "destructive_sql": (
            "⛔ Destructive database operations are not permitted through this interface."
        ),
        "unauthorized_write": (
            "⛔ Unauthorized data modification attempts are blocked."
        ),
        "code_injection": (
            "⛔ Code execution commands are not allowed."
        ),
        "scope_escalation": (
            "⛔ I'm scoped to enterprise revenue recovery tasks only. "
            "I can't help with that request."
        ),
        "destructive_command": (
            "⛔ System-level destructive commands are blocked."
        ),
        "network_exfiltration": (
            "⛔ External network requests to unknown domains are not permitted."
        ),
    }
    return messages.get(category, "⛔ This query was blocked by the security guardrail.")
