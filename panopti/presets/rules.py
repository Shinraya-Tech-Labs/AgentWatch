"""Pre-packaged destructive command presets for Panopti.

Each category is a list of :class:`~panoptis.core.safety.RiskPattern` objects, so
they can be passed straight to ``RiskScorer(extra_patterns=...)``,
``SafetyPolicy(custom_patterns=...)`` or ``PanoptiGuard(extra_patterns=...)``.

The presets are opt-in on purpose. They only add what ``BUILTIN_RISK_PATTERNS`` does
not already catch (``rm -rf /``, ``mkfs``, ``dd ... of=/dev/``, ``DROP TABLE``,
``TRUNCATE TABLE``, ``DELETE FROM x;``), so a command is not reported twice.

Matching is done by ``RiskScorer`` with ``re.search`` and ``re.IGNORECASE`` on the raw
command text. Patterns that have a safe "dry run" form skip it with a lookahead.
"""

from __future__ import annotations

from panoptis.core.safety import RiskPattern
from panoptis.core.schema import RiskLevel

SQL_DESTRUCTIVE = "sql_destructive"
SHELL_DESTRUCTIVE = "shell_destructive"
CLOUD_DESTRUCTIVE = "cloud_destructive"
CONTAINER_DESTRUCTIVE = "container_destructive"


def _p(
    pattern: str,
    level: RiskLevel,
    reason: str,
    policy_id: str,
    *,
    block: bool = False,
) -> RiskPattern:
    return RiskPattern(
        pattern=pattern,
        risk_level=level,
        reason=reason,
        policy_id=policy_id,
        block_by_default=block,
    )


_HIGH = RiskLevel.HIGH
_CRIT = RiskLevel.CRITICAL

# A bare table name, optionally schema-qualified or quoted.
_TABLE = r"[\w.\"`\[\]]+"

_SQL: list[RiskPattern] = [
    _p(
        # "TRUNCATE users;" without the TABLE keyword (TRUNCATE TABLE is a builtin).
        rf"\bTRUNCATE\s+(?!TABLE\b)(?:ONLY\s+)?{_TABLE}(?=\s*[;,\x27\"]|\s+(?:CASCADE|RESTART|CONTINUE)\b|(?:\s+\S+)?\s*$)",
        _HIGH,
        "TRUNCATE without TABLE keyword removes every row",
        "SQL_TRUNCATE",
    ),
    _p(
        # DELETE with no WHERE clause before the end of the statement. The builtin only
        # catches "DELETE FROM x;". The scorer appends the tool name to the command, so
        # an end-of-text anchor cannot be used here.
        rf"\bDELETE\s+FROM\s+(?:ONLY\s+)?{_TABLE}(?![^;]*\bWHERE\b)",
        _HIGH,
        "DELETE FROM without a WHERE clause",
        "SQL_DELETE_UNBOUNDED",
    ),
    _p(
        rf"\bUPDATE\s+{_TABLE}\s+SET\s+(?![^;]*\bWHERE\b)[^;]+",
        _HIGH,
        "UPDATE without a WHERE clause",
        "SQL_UPDATE_UNBOUNDED",
    ),
    _p(
        r"\bDROP\s+SCHEMA\b",
        _CRIT,
        "Dropping a database schema",
        "SQL_DROP_SCHEMA",
        block=True,
    ),
]

_SHELL: list[RiskPattern] = [
    _p(
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        _CRIT,
        "Fork bomb",
        "SHELL_FORK_BOMB",
        block=True,
    ),
    _p(
        r">\s*/dev/(?:sd[a-z]|hd[a-z]|vd[a-z]|xvd[a-z]|nvme\d)",
        _CRIT,
        "Writing directly to a block device",
        "SHELL_BLOCK_DEVICE_WRITE",
        block=True,
    ),
    _p(
        r"\bwipefs\b[^\n]*/dev/",
        _CRIT,
        "Wiping filesystem signatures from a device",
        "SHELL_WIPEFS",
        block=True,
    ),
    _p(
        r"\bshred\b[^\n]*(?:/dev/|\s-\w*u)",
        _CRIT,
        "Securely destroying a device or file",
        "SHELL_SHRED",
        block=True,
    ),
    _p(
        r"\bfind\s+(?:/|~|\$HOME)(?:\s|/\s)[^\n]*(?:-delete\b|-exec\s+rm\b)",
        _CRIT,
        "find with -delete or rm starting at the filesystem root or home",
        "SHELL_FIND_DELETE",
        block=True,
    ),
    _p(
        r"\bchown\s+(?:-\w+\s+)*-R\b[^\n]*\s/(?:\s|$)",
        _CRIT,
        "Recursive ownership change on the filesystem root",
        "SHELL_CHOWN_ROOT",
        block=True,
    ),
    _p(
        r"\bkill\s+-(?:9|KILL)\s+-1\b",
        _HIGH,
        "Killing every process the user can signal",
        "SHELL_KILL_ALL",
    ),
    _p(
        r"(?:^|[;&|]\s*|\bsudo\s+)(?:shutdown|poweroff|halt|reboot)\b",
        _HIGH,
        "Shutting down or rebooting the host",
        "SHELL_HOST_POWER",
    ),
]

_CLOUD: list[RiskPattern] = [
    _p(
        r"\baws\s+s3\s+rb\b[^\n]*--force",
        _CRIT,
        "Force-removing an S3 bucket with all its objects",
        "AWS_S3_RB_FORCE",
        block=True,
    ),
    _p(
        r"\baws\s+s3\s+rb\b(?![^\n]*--force)",
        _HIGH,
        "Removing an S3 bucket",
        "AWS_S3_RB",
    ),
    _p(
        r"\baws\s+s3\s+rm\b(?=[^\n]*--recursive)(?![^\n]*--dryrun)",
        _HIGH,
        "Recursive S3 delete",
        "AWS_S3_RM_RECURSIVE",
    ),
    _p(
        r"\baws\s+s3api\s+delete-bucket\b",
        _HIGH,
        "Deleting an S3 bucket",
        "AWS_S3API_DELETE_BUCKET",
    ),
    _p(
        r"\baws\s+rds\s+delete-db-(?:instance|cluster)\b",
        _CRIT,
        "Deleting an RDS database",
        "AWS_RDS_DELETE",
        block=True,
    ),
    _p(
        r"\baws\s+ec2\s+terminate-instances\b",
        _HIGH,
        "Terminating EC2 instances",
        "AWS_EC2_TERMINATE",
    ),
    _p(
        r"\baws\s+(?:dynamodb\s+delete-table|eks\s+delete-cluster|cloudformation\s+delete-stack)\b",
        _HIGH,
        "Deleting a DynamoDB table, EKS cluster or CloudFormation stack",
        "AWS_DELETE_RESOURCE",
    ),
    _p(
        r"\bgcloud\s+projects\s+delete\b",
        _CRIT,
        "Deleting a Google Cloud project",
        "GCP_PROJECT_DELETE",
        block=True,
    ),
    _p(
        r"\bgcloud\s+(?:compute\s+instances|sql\s+instances|container\s+clusters)\s+delete\b",
        _HIGH,
        "Deleting a Google Cloud VM, Cloud SQL instance or GKE cluster",
        "GCP_RESOURCE_DELETE",
    ),
    _p(
        r"\bgsutil\s+(?:-\w+\s+)*rm\s+(?:-\w+\s+)*-\w*r|\bgsutil\s+(?:-\w+\s+)*rb\b",
        _HIGH,
        "Recursive delete or removal of a Cloud Storage bucket",
        "GCP_GSUTIL_DELETE",
    ),
    _p(
        r"\baz\s+group\s+delete\b",
        _CRIT,
        "Deleting an Azure resource group",
        "AZ_GROUP_DELETE",
        block=True,
    ),
    _p(
        r"\baz\s+(?:vm|aks|sql\s+(?:server|db)|storage\s+account)\s+delete\b",
        _HIGH,
        "Deleting an Azure VM, AKS cluster, SQL resource or storage account",
        "AZ_RESOURCE_DELETE",
    ),
    _p(
        r"\b(?:terraform|tofu|pulumi)\s+destroy\b",
        _HIGH,
        "Destroying all managed infrastructure",
        "IAC_DESTROY",
    ),
]

_CONTAINER: list[RiskPattern] = [
    _p(
        r"\bdocker\s+system\s+prune\b[^\n]*--volumes",
        _CRIT,
        "Pruning all unused Docker data including volumes",
        "DOCKER_PRUNE_VOLUMES",
        block=True,
    ),
    _p(
        r"\bdocker\s+system\s+prune\b(?![^\n]*--volumes)[^\n]*(?:\s-\w*a\b|--all\b)",
        _HIGH,
        "Pruning all unused Docker images and containers",
        "DOCKER_PRUNE_ALL",
    ),
    _p(
        r"\bdocker\s+volume\s+(?:rm|prune)\b",
        _HIGH,
        "Removing Docker volumes",
        "DOCKER_VOLUME_DELETE",
    ),
    _p(
        r"\bdocker\s+(?:rm|rmi|kill|stop)\b[^\n]*(?:\$\(|`)\s*docker\s+(?:ps|images)\b",
        _HIGH,
        "Removing or stopping every container or image at once",
        "DOCKER_BULK_REMOVE",
    ),
    _p(
        r"\bkubectl\s+delete\s+(?:ns|namespaces?)\b(?![^\n]*--dry-run)",
        _CRIT,
        "Deleting a Kubernetes namespace and everything in it",
        "K8S_DELETE_NAMESPACE",
        block=True,
    ),
    _p(
        r"\bkubectl\s+delete\b(?![^\n]*--dry-run)[^\n]*(?:--all\b|--all-namespaces\b|\s-A\b)",
        _HIGH,
        "Deleting all Kubernetes resources of a kind",
        "K8S_DELETE_ALL",
    ),
    _p(
        r"\bkubectl\s+delete\s+(?:pv|pvc|persistentvolumes?|persistentvolumeclaims?|nodes?)\b",
        _HIGH,
        "Deleting persistent volumes, claims or nodes",
        "K8S_DELETE_STORAGE_OR_NODE",
    ),
    _p(
        r"\bhelm\s+(?:uninstall|delete)\b",
        _HIGH,
        "Uninstalling a Helm release",
        "HELM_UNINSTALL",
    ),
]

PRESET_CATEGORIES: dict[str, list[RiskPattern]] = {
    SQL_DESTRUCTIVE: _SQL,
    SHELL_DESTRUCTIVE: _SHELL,
    CLOUD_DESTRUCTIVE: _CLOUD,
    CONTAINER_DESTRUCTIVE: _CONTAINER,
}


def get_preset_patterns(*categories: str) -> list[RiskPattern]:
    """Return the preset risk patterns for the given categories.

    Args:
        *categories: Any of ``sql_destructive``, ``shell_destructive``,
            ``cloud_destructive`` and ``container_destructive``. With no arguments
            every category is returned.

    Returns:
        A new list of :class:`RiskPattern`, ready for ``extra_patterns=``.

    Raises:
        ValueError: If a category name is not known.
    """
    if not categories:
        categories = tuple(PRESET_CATEGORIES)
    unknown = [c for c in categories if c not in PRESET_CATEGORIES]
    if unknown:
        valid = ", ".join(sorted(PRESET_CATEGORIES))
        raise ValueError(f"Unknown preset categories {unknown}; valid categories: {valid}")
    patterns: list[RiskPattern] = []
    for name in dict.fromkeys(categories):
        patterns.extend(PRESET_CATEGORIES[name])
    return patterns
