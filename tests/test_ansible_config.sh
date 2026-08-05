#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# The remote temporary directory must follow the active SSH/become user.
# A shared 0700 directory under /tmp breaks tasks that switch to deploy.
grep -Fqx 'remote_tmp = ~/.ansible/tmp' "$ROOT_DIR/ansible/ansible.cfg"
printf '%s\n' 'Ansible remote temporary directory configuration passed.'
