#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# The remote temporary directory must follow the active SSH/become user.
# A shared 0700 directory under /tmp breaks tasks that switch to deploy.
grep -Fqx 'remote_tmp = ~/.ansible/tmp' "$ROOT_DIR/ansible/ansible.cfg"
grep -Fq '      ANSIBLE_CONFIG: ansible/ansible.cfg' "$ROOT_DIR/.github/workflows/checks.yml"
printf '%s\n' 'Ansible remote temporary directory configuration passed.'
