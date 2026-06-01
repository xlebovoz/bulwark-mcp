"""Capability filter — a coarse, name-based tool allowlist.

This is a separate, content-blind access-control layer that sits in front
of the detection cascade (rules + LLM). It looks ONLY at the *name* of the
tool a client wants to call and blocks names that are not on an explicit
allowlist. Argument *content* is the rules layer's job (ADR-0004); this
module never inspects arguments.

Design (intentionally minimal — see the task brief):

- **Fail-open.** With no allowlist configured, every tool call passes
  through (``reason="no_allowlist"``). The proxy emits a loud startup
  warning and ``bulwark doctor`` surfaces the inactive state, so the open
  default is never silent.
- **Exact match only.** Names are ``<server>.<tool>`` namespaced (e.g.
  ``filesystem.read``). No wildcards, globs, prefixes, or case folding.
- **Synchronous.** A membership test is microseconds; no async needed.

``allowed_tools`` is stored as a ``tuple`` rather than a ``list`` so the
settings object stays immutable/hashable like the other frozen dataclasses
in :mod:`bulwark_mcp.config`; this is an internal representation choice, not
a behavioural one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CapabilityReason = Literal["no_allowlist", "in_allowlist", "not_in_allowlist"]


@dataclass(frozen=True)
class CapabilitySettings:
    """Capability-filter config (parsed from the top-level ``capability:``
    YAML section, parallel to ``storage:`` / ``detector:``).

    ``server_name`` is prepended to each incoming tool name to form the
    ``<server>.<tool>`` key matched against ``allowed_tools``. The allowlist
    is YAML-only: list-valued env vars are too awkward to be worth the
    surface area, so there is deliberately no env-var or CLI override.
    """

    allowed_tools: tuple[str, ...] = ()
    server_name: str = ""


@dataclass(frozen=True)
class CapabilityDecision:
    """Outcome of one :meth:`CapabilityFilter.check`.

    ``reason`` is machine-readable (used by the audit log and tests):
    ``no_allowlist`` (fail-open), ``in_allowlist`` (allowed), or
    ``not_in_allowlist`` (blocked).
    """

    allowed: bool
    reason: CapabilityReason


class CapabilityFilter:
    """Name-based allowlist. Construction takes a :class:`CapabilitySettings`."""

    def __init__(self, settings: CapabilitySettings) -> None:
        # Collapse duplicates — membership is the only thing that matters.
        self._allowed: frozenset[str] = frozenset(settings.allowed_tools)
        self._server_name = settings.server_name

    @property
    def active(self) -> bool:
        """``True`` when an allowlist is configured (i.e. NOT fail-open)."""
        return bool(self._allowed)

    @property
    def server_name(self) -> str:
        return self._server_name

    def namespaced(self, tool_name: str) -> str:
        """Build the ``<server>.<tool>`` key matched against the allowlist.

        With no ``server_name`` configured the bare tool name is returned
        unchanged — which will not match any ``<server>.<tool>`` allowlist
        entry, so configuring an allowlist also means configuring
        ``server_name``.
        """
        if self._server_name:
            return f"{self._server_name}.{tool_name}"
        return tool_name

    def check(self, tool_name: str) -> CapabilityDecision:
        """Decide whether the already-namespaced ``tool_name`` may pass.

        ``tool_name`` is expected to be the full ``<server>.<tool>`` key
        (the proxy builds it via :meth:`namespaced`). An empty allowlist is
        the fail-open default. Matching is exact: ``filesystem.read`` does
        not match ``filesystem.read_file``.
        """
        if not self._allowed:
            return CapabilityDecision(allowed=True, reason="no_allowlist")
        if tool_name in self._allowed:
            return CapabilityDecision(allowed=True, reason="in_allowlist")
        return CapabilityDecision(allowed=False, reason="not_in_allowlist")
