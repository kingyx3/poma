"""Refuse routine deployments that would discard the VM's durable trading state.

Consumes `terraform show -json tfplan` on stdin; never prints plan values/secrets.
Explicit undeploy is handled separately by the workflow.
"""

from __future__ import annotations

import json
import sys

PROTECTED_TYPES = {"google_compute_instance", "google_compute_disk", "google_compute_region_disk"}
SAFE_ACTIONS = [["no-op"], ["create"], ["read"], ["update"]]


def validate_plan(plan: object) -> None:
    if not isinstance(plan, dict):
        raise ValueError("expected a Terraform JSON plan object")
    version = plan.get("format_version", "")
    if not isinstance(version, str) or not version.startswith("1."):
        raise ValueError("unsupported Terraform JSON plan format")
    if not isinstance(plan.get("planned_values"), dict):
        raise ValueError("missing Terraform planned values")
    if plan.get("errored") or plan.get("complete") is False:
        raise ValueError("Terraform plan is errored or incomplete")
    changes = plan.get("resource_changes", [])
    if not isinstance(changes, list):
        raise ValueError("invalid Terraform resource changes")
    for resource in changes:
        if not isinstance(resource, dict) or resource.get("mode") not in {"managed", "data"}:
            raise ValueError("invalid Terraform resource change")
        if not isinstance(resource.get("type"), str) or not isinstance(resource.get("change"), dict):
            raise ValueError("missing Terraform resource change metadata")
        if (
            resource["mode"] == "managed"
            and resource["type"] in PROTECTED_TYPES
            and resource["change"].get("actions") not in SAFE_ACTIONS
        ):
            raise ValueError(
                "deployment would delete, replace or forget a VM/disk, or uses an unknown action; "
                "refusing to risk local order/state/data loss. Pause trading, back up and verify "
                "restoration, then perform a reviewed migration. Explicit undeploy is destructive."
            )


def main() -> int:
    try:
        validate_plan(json.load(sys.stdin))
    except (ValueError, TypeError) as exc:
        # JSONDecodeError includes source fragments in some contexts. Keep the
        # parser error generic because Terraform JSON may contain credentials.
        message = "invalid Terraform JSON" if isinstance(exc, json.JSONDecodeError) else str(exc)
        print(f"Deployment plan rejected: {message}", file=sys.stderr)
        return 1
    print("Deployment plan preserves existing VM and disk resources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
