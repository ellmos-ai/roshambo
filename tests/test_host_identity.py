"""Regression tests for registry-backed, host-qualified field-run identities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from demo.multivendor.collect_evidence import Event, analyse, summarise, vendor_of
from demo.multivendor.run_field import render_prompt
from roshambo.cli import _build_parser
from roshambo.db import _is_duplicate_identity_constraint, find_schema_file


def test_cli_exposes_registration_and_decision_verbs():
    registered = _build_parser().parse_args(
        [
            "register-agent",
            "--agent-id",
            "codex-2@host-a",
            "--framework",
            "codex",
            "--host",
            "host-a",
        ]
    )
    assert registered.command == "register-agent"
    assert registered.agent_id == "codex-2@host-a"
    assert _build_parser().parse_args(
        [
            "decide",
            "ship?",
            "--choice",
            "wait",
            "--rationale",
            "external gate open",
            "--confidence",
            "high",
            "--provenance",
            "human-confirmed",
        ]
    ).command == "decide"


def test_field_prompt_registers_stable_host_qualified_identity(tmp_path: Path):
    rendered = render_prompt(
        "codex-2@host-a",
        "codex",
        "host-a",
        tmp_path,
        tmp_path / "rsb.cmd",
        120,
    )
    assert (
        "register-agent --agent-id codex-2@host-a --framework codex --host host-a"
        in rendered
    )
    assert "Continue only on `RESULT=REGISTERED`" in rendered
    assert "{AGENT_ID}" not in rendered
    assert "{HOST_LABEL}" not in rendered


def test_vendor_detection_ignores_instance_and_host_suffixes():
    assert vendor_of("codex-2@workstation-a") == "openai"
    assert vendor_of("claude-code@workstation-b") == "anthropic"
    assert vendor_of("agy-3@workstation-a") == "google"
    assert vendor_of("unknown@workstation-a") is None


def test_audit_event_carries_immutable_identity_snapshot():
    event = Event(
        created_at=SimpleNamespace(),
        agent_id="codex@host-a",
        resource="repo:file",
        allowed=True,
        reason=None,
        framework_snapshot="codex",
        host_snapshot="host-a",
    )
    assert (event.framework_snapshot, event.host_snapshot) == ("codex", "host-a")


def test_cross_host_collision_requires_different_grant_and_denial_snapshots():
    started = datetime(2026, 7, 28, tzinfo=timezone.utc)
    events = [
        Event(
            started,
            "codex@host-a",
            "fieldkit:task:01",
            True,
            None,
            "codex",
            "host-a",
        ),
        Event(
            started + timedelta(seconds=2),
            "agy@host-b",
            "fieldkit:task:01",
            False,
            "held by codex@host-a",
            "agy",
            "host-b",
        ),
    ]
    summary = summarise(
        analyse(events, 120),
        {
            "trails": 0,
            "trail_failures": 0,
            "audit_rows": 2,
            "distinct_agents": 2,
            "distinct_hosts": 2,
        },
        120,
    )
    task = summary["task_resources"]
    assert task["genuine_collisions"] == 1
    assert task["cross_host_collisions"] == 1
    assert task["cross_host_events"] == 1


def test_two_registered_hosts_without_cross_host_contention_do_not_prove_it():
    started = datetime(2026, 7, 28, tzinfo=timezone.utc)
    events = [
        Event(started, "codex@host-a", "fieldkit:task:01", True, None, "codex", "host-a"),
        Event(
            started + timedelta(seconds=2),
            "agy@host-a",
            "fieldkit:task:01",
            False,
            "held by codex@host-a",
            "agy",
            "host-a",
        ),
    ]
    analysis = analyse(events, 120)["task"]
    assert len(analysis.collisions) == 1
    assert analysis.cross_host_events == 0


def test_schema_links_claim_and_audit_ids_to_registry_keys():
    schema = find_schema_file().read_text(encoding="utf-8")
    assert "UNIQUE INDEX agents_by_key (swarm_id, agent_key)" in schema
    assert "CONSTRAINT claims_agent_fk FOREIGN KEY (swarm_id, agent_id)" in schema
    assert "CONSTRAINT audit_agent_fk FOREIGN KEY (swarm_id, agent_id)" in schema
    assert "framework_snapshot STRING NOT NULL" in schema
    assert "host_snapshot STRING NOT NULL" in schema
    assert "SET agent_key = agent_id::STRING" in schema
    assert "WHERE agent_key IS NULL" in schema


def test_only_named_duplicate_fk_constraints_are_idempotently_skipped():
    duplicate = SimpleNamespace(sqlstate="42710")
    assert _is_duplicate_identity_constraint(
        "ALTER TABLE claims ADD CONSTRAINT claims_agent_fk FOREIGN KEY (x) REFERENCES y (x)",
        duplicate,
    )
    assert not _is_duplicate_identity_constraint(
        "ALTER TABLE claims ADD CONSTRAINT unrelated FOREIGN KEY (x) REFERENCES y (x)",
        duplicate,
    )
    assert not _is_duplicate_identity_constraint(
        "ALTER TABLE claims ADD CONSTRAINT claims_agent_fk FOREIGN KEY (x) REFERENCES y (x)",
        SimpleNamespace(sqlstate="23503"),
    )
