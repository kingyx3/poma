from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_TF = REPO_ROOT / "infra/gcp-free-tier/main.tf"


def test_vm_recreates_when_startup_script_changes() -> None:
    """The VM must be replaced when startup.sh changes so the new bootstrap runs on a clean boot.

    GCE only runs the startup script at boot and an in-place metadata update does not reboot the
    instance, so without this the committed bootstrap can silently drift from the running VM.
    """
    tf = MAIN_TF.read_text(encoding="utf-8")

    assert 'resource "terraform_data" "startup_revision"' in tf
    assert "startup_revision = md5(join" in tf
    assert "local.app_uid" in tf
    assert "local.app_gid" in tf
    assert "input = local.startup_revision" in tf
    assert "poma-startup-revision  = local.startup_revision" in tf
    assert "replace_triggered_by = [terraform_data.startup_revision]" in tf
    # The metadata and the replacement hash must share one rendered script.
    assert "startup-script         = local.startup_script" in tf
