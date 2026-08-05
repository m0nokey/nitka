#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RENDER_DIR="$(mktemp -d)"
trap 'rm -rf "$RENDER_DIR"' EXIT

ansible-playbook \
    "$ROOT_DIR/tests/ansible/render_templates.yml" \
    -e "render_dir=$RENDER_DIR" \
    >/dev/null

python3 - "$RENDER_DIR" <<'PY'
import json
import pathlib
import sys

import yaml

render_dir = pathlib.Path(sys.argv[1])
config = json.loads((render_dir / "config.json").read_text(encoding="utf-8"))
compose = yaml.safe_load((render_dir / "compose.yml").read_text(encoding="utf-8"))
unbound = (render_dir / "unbound.conf").read_text(encoding="utf-8")
unbound_dockerfile = (render_dir / "unbound.Dockerfile").read_text(encoding="utf-8")
ssh_config = (render_dir / "ssh-sshd_config").read_text(encoding="utf-8")
ssh_compose = yaml.safe_load((render_dir / "ssh-compose.yml").read_text(encoding="utf-8"))
ssh_dockerfile = (render_dir / "ssh-Dockerfile").read_text(encoding="utf-8")
ssh_entrypoint = (render_dir / "ssh-entrypoint.sh").read_text(encoding="utf-8")
ssh_transition = (render_dir / "ssh-transition.conf").read_text(encoding="utf-8")

assert len(config["inbounds"]) == 2
assert config["inbounds"][0]["streamSettings"]["network"] == "tcp"
assert config["inbounds"][1]["streamSettings"]["network"] == "xhttp"
assert config["inbounds"][1]["streamSettings"]["xhttpSettings"]["mode"] == "packet-up"
assert config["dns"]["servers"][0]["port"] == 5353
assert any(rule.get("outboundTag") == "block" for rule in config["routing"]["rules"])

assert compose["services"]["xray"]["read_only"] is True
assert compose["services"]["xray"]["cap_drop"] == ["ALL"]
assert "no-new-privileges:true" in compose["services"]["xray"]["security_opt"]
assert compose["services"]["xray"]["depends_on"]["unbound"]["condition"] == "service_healthy"
assert compose["services"]["unbound"]["healthcheck"]["retries"] == 3

assert "forward-tls-upstream: yes" in unbound
assert "forward-addr: 1.1.1.1@853#cloudflare-dns.com" in unbound
assert "forward-addr: 94.140.14.140@853#unfiltered.adguard-dns.com" in unbound
assert unbound.count("rpz-action-override: nxdomain") == 2
assert unbound_dockerfile.startswith("FROM alpine:3.23\n")
assert "unbound" in unbound_dockerfile
assert ssh_compose["services"]["nitka-ssh-transport"]["ports"] == ["30422:2222"]
assert ssh_compose["services"]["nitka-ssh-transport"]["read_only"] is True
assert "nitka_ssh_transport_host_keys" in ssh_compose["volumes"]
assert ssh_dockerfile.startswith("FROM alpine:3.23\n")
assert "netcat-openbsd" in ssh_dockerfile
assert ssh_entrypoint.index("ssh-keygen") < ssh_entrypoint.index("= validate")
assert "AllowUsers abcdefgh1234 zyxwvuts9876" in ssh_config
assert "AuthorizedKeysFile /var/ssh/%u/authorized_keys" in ssh_config
assert "AllowTcpForwarding yes" in ssh_config
assert "PermitOpen any" in ssh_config
assert "PermitTunnel no" in ssh_config
assert "xray" not in ssh_config.lower()
assert "hermes" not in ssh_config.lower()
assert "abcdefgh1234" in ssh_dockerfile
assert "zyxwvuts9876" in ssh_dockerfile
assert 'authorized_key="$authorized_keys_dir/abcdefgh1234"' in ssh_entrypoint
assert 'authorized_key="$authorized_keys_dir/zyxwvuts9876"' in ssh_entrypoint
assert "Port 22" in ssh_transition
assert "Port 38251" in ssh_transition
assert "PasswordAuthentication yes" in ssh_transition
assert "AllowUsers debian deploy" in ssh_transition
PY

printf '%s\n' 'Ansible template tests passed.'
