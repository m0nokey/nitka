#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$ROOT_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
deployment = (root / "lib/deployment.sh").read_text(encoding="utf-8")
bootstrap = (root / "ansible/roles/system_base/tasks/bootstrap.yml").read_text(
    encoding="utf-8"
)
harden = (root / "ansible/roles/system_base/tasks/harden_ssh.yml").read_text(
    encoding="utf-8"
)
entrypoint = (root / "controller/entrypoint.sh").read_text(encoding="utf-8")
pipeline = (root / "lib/pipeline.sh").read_text(encoding="utf-8")

deploy_body = deployment[deployment.index("deploy_node() {") : deployment.index("cascade_deployment_for_node() {")]
assert deploy_body.index('"$ROOT_DIR/ansible/playbooks/deploy_standalone.yml"') < deploy_body.index(
    '"$ROOT_DIR/ansible/playbooks/harden_ssh.yml"'
)
assert deploy_body.index('"$ROOT_DIR/ansible/playbooks/harden_ssh.yml"') < deploy_body.index(
    '"$ROOT_DIR/ansible/playbooks/finalize_ssh.yml"'
)
assert '"$deployment_mode" == bootstrap-only' in deploy_body
assert 'hardening-only' in deploy_body
assert deployment.index('run_cascade_playbooks') < deployment.index(
    'deploy_node "$egress_node" "$cascade_state" "" 0 hardening-only'
)
assert "pending_install_begin" in deployment
assert "pending_install_sync" in deployment
assert "pending_install_commit" in deployment
assert "systemd-run" not in harden
assert "Install temporary SSH transition configuration" in harden
assert "scan_ssh_ed25519_host_key" in deployment
assert "never reuse the" in deploy_body.lower()
assert "candidate_ports" not in deploy_body
assert 'mktemp -d "$RUNTIME_TMP_DIR/nitka-inventory.XXXXXX"' in deploy_body
state_cli = (root / "scripts/state_cli.py").read_text(encoding="utf-8")
assert '"repair-access-port"' in state_cli
assert 'repair-access-port requires NODE' in state_cli
access_tasks = (root / "ansible/roles/transports/access/tasks/main.yml").read_text(
    encoding="utf-8"
)
ssh_tasks = (root / "ansible/roles/transports/access/ssh_proxy/tasks/main.yml").read_text(
    encoding="utf-8"
)
assert "access_transport_selection" in access_tasks
assert "access_ssh_proxy_external_port" in ssh_tasks
assert 'access_ssh_proxy_access_keys' in ssh_tasks
assert "Install final hardened sshd_config" in (root / "ansible/roles/system_base/tasks/finalize_ssh.yml").read_text(
    encoding="utf-8"
)
assert "tasks_from: finalize_ssh" in (root / "ansible/playbooks/finalize_ssh.yml").read_text(
    encoding="utf-8"
)
assert "src: sshd_config.j2" in (root / "ansible/roles/system_base/tasks/finalize_ssh.yml").read_text(
    encoding="utf-8"
)
finalize_tasks = (root / "ansible/roles/system_base/tasks/finalize_ssh.yml").read_text(
    encoding="utf-8"
)
assert "prepare_ssh_hardening.yml" in finalize_tasks
assert "prepare_ssh_hardening.yml" in harden
prepare_tasks = (root / "ansible/roles/system_base/tasks/prepare_ssh_hardening.yml").read_text(
    encoding="utf-8"
)
assert "system_base_harden_ssh_kex_algorithms" in prepare_tasks
assert "system_base_harden_ssh_allow_groups" in prepare_tasks
assert "finalize_ssh.yml" in pipeline
ssh_tasks = (root / "ansible/roles/transports/access/ssh_proxy/tasks/main.yml").read_text(
    encoding="utf-8"
)
xray_tasks = (root / "ansible/roles/transports/access/xray_reality/tasks/standalone.yml").read_text(encoding="utf-8")
assert "Wait for standalone SSH transport healthcheck" in ssh_tasks
assert "Remove stale standalone SSH transport container" in ssh_tasks
assert "Wait for Xray container healthcheck" in xray_tasks
ansible_shell = (root / "lib/ansible.sh").read_text(encoding="utf-8")
assert "if [[ \"$port\" == 22 ]]" in ansible_shell
assert "Save the original sshd_config before installation" in bootstrap
assert 'menu_option 1 "Resume previous installation"' in entrypoint
assert 'menu_option 2 "Abort and clean the VPS"' in entrypoint
assert 'menu_option 3 "Start over after manual cleanup"' in entrypoint
pending_cleanup = deployment[
    deployment.index("pending_install_cleanup()") : deployment.index(
        "recover_pending_installation()"
    )
]
assert '"$ROOT_DIR/ansible/playbooks/cleanup.yml"' in pending_cleanup
assert '"$ROOT_DIR/ansible/playbooks/remove.yml"' not in pending_cleanup

print("Install transaction and SSH ordering checks passed.")
PY
