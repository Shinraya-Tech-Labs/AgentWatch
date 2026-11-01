"""Tests for the pre-packaged destructive command presets."""

from __future__ import annotations

import re

import pytest

from panoptis.core.safety import BUILTIN_RISK_PATTERNS, RiskScorer, SafetyEngine, SafetyPolicy
from panoptis.core.schema import RiskLevel, ToolCallData
from panopti.guard import PanoptiGuard
from panopti.presets import (
    CLOUD_DESTRUCTIVE,
    CONTAINER_DESTRUCTIVE,
    PRESET_CATEGORIES,
    SHELL_DESTRUCTIVE,
    SQL_DESTRUCTIVE,
    get_preset_patterns,
)


def _score(command: str, *categories: str) -> tuple[RiskLevel, list[str]]:
    scorer = RiskScorer(extra_patterns=get_preset_patterns(*categories))
    call = ToolCallData(tool_name="bash", arguments={"cmd": command}, raw_command=command)
    level, _score_value, _reasons, policies = scorer.score(call)
    return level, policies


# (category, command, policy id that must be reported)
POSITIVES = [
    (SQL_DESTRUCTIVE, "TRUNCATE users;", "SQL_TRUNCATE"),
    (SQL_DESTRUCTIVE, "psql -c 'truncate orders'", "SQL_TRUNCATE"),
    (SQL_DESTRUCTIVE, "TRUNCATE users CASCADE", "SQL_TRUNCATE"),
    (SQL_DESTRUCTIVE, "DELETE FROM sessions", "SQL_DELETE_UNBOUNDED"),
    (SQL_DESTRUCTIVE, "delete from public.sessions;", "SQL_DELETE_UNBOUNDED"),
    (SQL_DESTRUCTIVE, "UPDATE accounts SET balance = 0;", "SQL_UPDATE_UNBOUNDED"),
    (SQL_DESTRUCTIVE, "DROP SCHEMA public CASCADE;", "SQL_DROP_SCHEMA"),
    (SHELL_DESTRUCTIVE, ":(){ :|:& };:", "SHELL_FORK_BOMB"),
    (SHELL_DESTRUCTIVE, "cat /dev/zero > /dev/sda", "SHELL_BLOCK_DEVICE_WRITE"),
    (SHELL_DESTRUCTIVE, "wipefs -a /dev/nvme0n1", "SHELL_WIPEFS"),
    (SHELL_DESTRUCTIVE, "shred -n 3 /dev/sdb", "SHELL_SHRED"),
    (SHELL_DESTRUCTIVE, "find / -name '*.log' -delete", "SHELL_FIND_DELETE"),
    (SHELL_DESTRUCTIVE, "find ~ -type f -exec rm {} +", "SHELL_FIND_DELETE"),
    (SHELL_DESTRUCTIVE, "chown -R nobody:nobody /", "SHELL_CHOWN_ROOT"),
    (SHELL_DESTRUCTIVE, "kill -9 -1", "SHELL_KILL_ALL"),
    (SHELL_DESTRUCTIVE, "sudo shutdown -h now", "SHELL_HOST_POWER"),
    (SHELL_DESTRUCTIVE, "echo done; reboot", "SHELL_HOST_POWER"),
    (CLOUD_DESTRUCTIVE, "aws s3 rb s3://prod-data --force", "AWS_S3_RB_FORCE"),
    (CLOUD_DESTRUCTIVE, "aws s3 rb s3://old-bucket", "AWS_S3_RB"),
    (CLOUD_DESTRUCTIVE, "aws s3 rm s3://prod-data --recursive", "AWS_S3_RM_RECURSIVE"),
    (CLOUD_DESTRUCTIVE, "aws s3api delete-bucket --bucket prod", "AWS_S3API_DELETE_BUCKET"),
    (
        CLOUD_DESTRUCTIVE,
        "aws rds delete-db-instance --db-instance-identifier prod",
        "AWS_RDS_DELETE",
    ),
    (CLOUD_DESTRUCTIVE, "aws ec2 terminate-instances --instance-ids i-123", "AWS_EC2_TERMINATE"),
    (CLOUD_DESTRUCTIVE, "aws dynamodb delete-table --table-name users", "AWS_DELETE_RESOURCE"),
    (CLOUD_DESTRUCTIVE, "gcloud projects delete my-project", "GCP_PROJECT_DELETE"),
    (CLOUD_DESTRUCTIVE, "gcloud compute instances delete vm-1 --zone z", "GCP_RESOURCE_DELETE"),
    (CLOUD_DESTRUCTIVE, "gcloud sql instances delete prod-db", "GCP_RESOURCE_DELETE"),
    (CLOUD_DESTRUCTIVE, "gsutil -m rm -r gs://prod-bucket", "GCP_GSUTIL_DELETE"),
    (CLOUD_DESTRUCTIVE, "az group delete --name prod-rg --yes", "AZ_GROUP_DELETE"),
    (CLOUD_DESTRUCTIVE, "az vm delete --name vm1 --resource-group rg", "AZ_RESOURCE_DELETE"),
    (CLOUD_DESTRUCTIVE, "terraform destroy -auto-approve", "IAC_DESTROY"),
    (CONTAINER_DESTRUCTIVE, "docker system prune -a --volumes", "DOCKER_PRUNE_VOLUMES"),
    (CONTAINER_DESTRUCTIVE, "docker system prune --all", "DOCKER_PRUNE_ALL"),
    (CONTAINER_DESTRUCTIVE, "docker volume rm pgdata", "DOCKER_VOLUME_DELETE"),
    (CONTAINER_DESTRUCTIVE, "docker rm -f $(docker ps -aq)", "DOCKER_BULK_REMOVE"),
    (CONTAINER_DESTRUCTIVE, "kubectl delete ns production", "K8S_DELETE_NAMESPACE"),
    (CONTAINER_DESTRUCTIVE, "kubectl delete namespace prod", "K8S_DELETE_NAMESPACE"),
    (CONTAINER_DESTRUCTIVE, "kubectl delete pods --all -n prod", "K8S_DELETE_ALL"),
    (CONTAINER_DESTRUCTIVE, "kubectl delete pvc data-0", "K8S_DELETE_STORAGE_OR_NODE"),
    (CONTAINER_DESTRUCTIVE, "helm uninstall payments", "HELM_UNINSTALL"),
]

# Read-only or bounded commands that must never be flagged by any preset.
BENIGN = [
    "aws s3 ls",
    "aws s3 cp file.txt s3://bucket/file.txt",
    "aws s3 rm s3://bucket/key.txt",
    "aws s3 rm s3://bucket --recursive --dryrun",
    "aws rds describe-db-instances",
    "aws ec2 describe-instances",
    "gcloud compute instances list",
    "gcloud projects list",
    "gsutil ls gs://bucket",
    "az group list",
    "az vm list",
    "terraform plan",
    "terraform plan -destroy",
    "terraform apply",
    "docker ps",
    "docker ps -a",
    "docker images",
    "docker logs web",
    "docker rm old-container",
    "docker system prune",
    "docker system df",
    "kubectl get ns",
    "kubectl get pods --all-namespaces",
    "kubectl describe pod web-1",
    "kubectl delete pod web-1",
    "kubectl delete ns staging --dry-run=client",
    "helm list",
    "helm install web ./chart",
    "SELECT * FROM users;",
    "DELETE FROM users WHERE id = 1;",
    "DELETE FROM users WHERE id = 1",
    "UPDATE users SET name = 'a' WHERE id = 2;",
    "INSERT INTO users (name) VALUES ('a');",
    "ls -la /",
    "find . -name '*.pyc'",
    "find / -name 'config.yml'",
    "chown -R app:app ./build",
    "kill -9 12345",
    "echo shutdown scheduled by operator",
    "cat /dev/null",
    "truncate the log file later",
]


@pytest.mark.parametrize(("category", "command", "policy_id"), POSITIVES)
def test_destructive_command_is_flagged(category: str, command: str, policy_id: str) -> None:
    level, policies = _score(command, category)
    assert policy_id in policies, f"{command!r} matched {policies}"
    assert level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


@pytest.mark.parametrize("command", BENIGN)
def test_benign_command_is_not_flagged_by_any_preset(command: str) -> None:
    preset_ids = {p.policy_id for p in get_preset_patterns()}
    _level, policies = _score(command)
    assert not preset_ids.intersection(policies), f"{command!r} matched {policies}"


def test_blocking_patterns_are_critical() -> None:
    for pattern in get_preset_patterns():
        if pattern.block_by_default:
            assert pattern.risk_level == RiskLevel.CRITICAL, pattern.policy_id


def test_every_category_is_non_empty_and_has_unique_policy_ids() -> None:
    seen: set[str] = set()
    for name, patterns in PRESET_CATEGORIES.items():
        assert patterns, name
        for pattern in patterns:
            assert pattern.policy_id not in seen, pattern.policy_id
            seen.add(pattern.policy_id)


def test_all_patterns_compile() -> None:
    for pattern in get_preset_patterns():
        re.compile(pattern.pattern, re.IGNORECASE)


def test_presets_do_not_reuse_builtin_policy_ids() -> None:
    builtin_ids = {p.policy_id for p in BUILTIN_RISK_PATTERNS}
    assert not builtin_ids.intersection(p.policy_id for p in get_preset_patterns())


def test_get_preset_patterns_selects_categories() -> None:
    assert get_preset_patterns(SQL_DESTRUCTIVE) == PRESET_CATEGORIES[SQL_DESTRUCTIVE]
    both = get_preset_patterns(SQL_DESTRUCTIVE, CLOUD_DESTRUCTIVE)
    assert len(both) == len(PRESET_CATEGORIES[SQL_DESTRUCTIVE]) + len(
        PRESET_CATEGORIES[CLOUD_DESTRUCTIVE]
    )


def test_get_preset_patterns_without_arguments_returns_everything() -> None:
    total = sum(len(patterns) for patterns in PRESET_CATEGORIES.values())
    assert len(get_preset_patterns()) == total


def test_get_preset_patterns_ignores_repeated_categories() -> None:
    once = get_preset_patterns(SQL_DESTRUCTIVE)
    assert get_preset_patterns(SQL_DESTRUCTIVE, SQL_DESTRUCTIVE) == once


def test_get_preset_patterns_returns_a_new_list() -> None:
    patterns = get_preset_patterns(SQL_DESTRUCTIVE)
    patterns.clear()
    assert get_preset_patterns(SQL_DESTRUCTIVE)


def test_unknown_category_raises_with_valid_names() -> None:
    with pytest.raises(ValueError, match="valid categories"):
        get_preset_patterns("nope")


def test_category_only_matches_its_own_commands() -> None:
    _level, policies = _score("kubectl delete ns production", SQL_DESTRUCTIVE)
    assert "K8S_DELETE_NAMESPACE" not in policies


def test_scorer_without_presets_does_not_flag_cloud_commands() -> None:
    call = ToolCallData(
        tool_name="bash",
        arguments={"cmd": "gcloud projects delete p"},
        raw_command="gcloud projects delete p",
    )
    level, _score_value, _reasons, _policies = RiskScorer().score(call)
    assert level == RiskLevel.SAFE


def test_safety_policy_custom_patterns_block_critical_preset() -> None:
    policy = SafetyPolicy(
        policy_id="presets",
        name="Presets",
        custom_patterns=get_preset_patterns(CLOUD_DESTRUCTIVE),
    )
    engine = SafetyEngine(policy=policy)
    call = ToolCallData(
        tool_name="bash",
        arguments={"cmd": "gcloud projects delete prod"},
        raw_command="gcloud projects delete prod",
    )
    blocked, reasons = engine.check_tool_call_sync(call)
    assert blocked
    assert any("Google Cloud project" in reason for reason in reasons)


def test_guard_without_presets_keeps_default_behaviour() -> None:
    decision = PanoptiGuard().check("bash", {"cmd": "gcloud projects delete prod"})
    assert decision.allowed


def test_guard_with_presets_blocks_critical_command() -> None:
    guard = PanoptiGuard(extra_patterns=get_preset_patterns())
    decision = guard.check("bash", {"cmd": "gcloud projects delete prod"})
    assert decision.blocked
    assert decision.risk_level == "CRITICAL"
    assert decision.reason == "Deleting a Google Cloud project"


def test_guard_with_presets_allows_benign_command() -> None:
    guard = PanoptiGuard(extra_patterns=get_preset_patterns())
    decision = guard.check("bash", {"cmd": "kubectl get ns"})
    assert decision.allowed
    assert decision.risk_level == "SAFE"
