from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ops.scripts.validate_deployment_plan import validate_plan


def plan_for(actions, resource_type='google_compute_instance', mode='managed'):
    return {
        'format_version': '1.2',
        'planned_values': {},
        'resource_changes': [{
            'mode': mode, 'type': resource_type, 'change': {'actions': actions},
        }],
    }


@pytest.mark.parametrize('actions', [['no-op'], ['create'], ['update']])
def test_routine_vm_changes_are_allowed(actions) -> None:
    validate_plan(plan_for(actions))


@pytest.mark.parametrize('resource_type', [
    'google_compute_instance', 'google_compute_disk', 'google_compute_region_disk',
])
@pytest.mark.parametrize('actions', [
    ['delete'], ['delete', 'create'], ['create', 'delete'], ['forget'], ['future-action'], None,
])
def test_state_destructive_or_unknown_actions_are_blocked(resource_type, actions) -> None:
    with pytest.raises(ValueError, match='refusing to risk local order/state/data loss'):
        validate_plan(plan_for(actions, resource_type))


def test_unrelated_replacement_and_data_reads_are_allowed() -> None:
    validate_plan(plan_for(['delete', 'create'], 'terraform_data'))
    validate_plan(plan_for(['read'], mode='data'))
    validate_plan({'format_version': '1.0', 'planned_values': {}})


@pytest.mark.parametrize('plan', [
    None, {}, {'format_version': '2.0', 'planned_values': {}},
    {'format_version': '1.2'},
    {'format_version': '1.2', 'planned_values': {}, 'resource_changes': None},
    {'format_version': '1.2', 'planned_values': {}, 'resource_changes': [{}]},
    {'format_version': '1.2', 'planned_values': {}, 'errored': True},
    {'format_version': '1.2', 'planned_values': {}, 'complete': False},
])
def test_malformed_or_incomplete_plan_fails_closed(plan) -> None:
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_script_rejects_without_printing_plan_values() -> None:
    plan = plan_for(['delete', 'create'])
    plan['planned_values'] = {'secret': 'do-not-print-this'}
    result = subprocess.run(
        [sys.executable, 'ops/scripts/validate_deployment_plan.py'],
        input=json.dumps(plan), text=True, capture_output=True, check=False,
    )
    assert result.returncode == 1
    assert 'refusing to risk' in result.stderr
    assert 'do-not-print-this' not in result.stdout + result.stderr


def test_workflow_validates_exact_saved_plan_before_apply() -> None:
    workflow = Path('.github/workflows/deploy-gcp-vm.yml').read_text()
    guard = workflow.index('- name: Protect durable runtime state')
    apply = workflow.index('- name: Terraform apply selected plan')
    assert guard < apply
    section = workflow[guard:apply]
    assert "inputs.deployment_action == 'deploy'" in section
    assert 'set -euo pipefail' in section
    assert 'terraform -chdir=infra/gcp-free-tier show -json tfplan' in section
    assert '| python3 ops/scripts/validate_deployment_plan.py' in section
