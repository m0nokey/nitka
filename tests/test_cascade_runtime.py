import os
import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class CascadeRuntimeTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_cascade_images_do_not_reuse_legacy_tags(self):
        files = (
            "ansible/roles/topology/cascade/egress/templates/compose.yml.j2",
            "ansible/roles/topology/cascade/egress/handlers/main.yml",
            "ansible/roles/topology/cascade/ingress/templates/compose.yml.j2",
            "ansible/roles/topology/cascade/ingress/templates/ingress_stack_ensure.sh.j2",
        )
        content = "\n".join(self.read(path) for path in files)
        for image in (
            "local/ssh-tun-server:latest",
            "local/ssh-tun-client:latest",
            "local/unbound:latest",
            "local/clash-rs:latest",
            "local/xray-core:auto",
        ):
            self.assertNotIn(image, content)

    def test_cascade_tun_uses_cascade_runtime_paths(self):
        compose = self.read("ansible/roles/topology/cascade/egress/templates/compose.yml.j2")
        dockerfile = self.read(
            "ansible/roles/transports/backhaul/ssh_tun/templates/server/Dockerfile.j2"
        )
        authorized_keys = self.read("ansible/roles/transports/backhaul/ssh_tun/tasks/server.yml")

        self.assertIn("/run/cascade/authorized_keys", compose)
        self.assertIn('authorized_keys_src="/run/cascade/authorized_keys"', dockerfile)
        self.assertNotIn("/run/nitka/authorized_keys", compose + dockerfile)
        self.assertNotIn('command=\\"/bin/false\\"', authorized_keys)
        self.assertIn("AllowTcpForwarding no", dockerfile)

    def test_ssh_tun_notifies_only_its_own_endpoint_handler(self):
        client = self.read("ansible/roles/transports/backhaul/ssh_tun/tasks/client.yml")
        server = self.read("ansible/roles/transports/backhaul/ssh_tun/tasks/server.yml")
        handlers = self.read("ansible/roles/transports/backhaul/ssh_tun/handlers/main.yml")

        self.assertNotIn("Restart ingress stack", client)
        self.assertNotIn("Restart egress stack", server)
        self.assertIn("notify: Restart SSH TUN endpoint service", client)
        self.assertIn("notify: Restart SSH TUN endpoint service", server)
        self.assertIn("name: Restart SSH TUN endpoint service", handlers)
        self.assertIn("tasks/restart.yml", handlers)

    def test_cascade_tun_migration_creates_parent_before_moving_legacy_path(self):
        tasks = self.read("ansible/roles/transports/backhaul/ssh_tun/tasks/main.yml")
        parent = tasks.index("Prepare canonical SSH TUN parent directory")
        migration = tasks.index("Migrate SSH TUN endpoint directory to canonical path")
        self.assertLess(parent, migration)
        self.assertIn("state: directory", tasks[parent:migration])

    def test_standalone_transport_migrations_create_parents_before_moving(self):
        migrations = (
            (
                "ansible/roles/transports/access/xray_reality/tasks/standalone.yml",
                "Create canonical Xray parent directory for migration",
                "Migrate Xray application directory to canonical path",
            ),
            (
                "ansible/roles/transports/access/ssh_proxy/tasks/main.yml",
                "Create canonical SSH proxy parent directory for migration",
                "Migrate SSH proxy application directory to canonical path",
            ),
        )
        for relative, parent_name, migration_name in migrations:
            tasks = self.read(relative)
            parent = tasks.index(parent_name)
            migration = tasks.index(migration_name)
            self.assertLess(parent, migration, relative)
            self.assertIn("state: directory", tasks[parent:migration], relative)
            self.assertIn("- mv", tasks[migration:], relative)

    def test_ingress_migration_conflict_check_uses_explicit_loop_index(self):
        tasks = self.read("ansible/roles/topology/cascade/ingress/tasks/main.yml")
        conflict = tasks.index("Reject conflicting ingress component directories")
        parents = tasks.index("Prepare canonical ingress component parent directories")
        migration = tasks.index("Migrate ingress component directories to canonical paths")
        block = tasks[conflict:migration]
        self.assertLess(conflict, parents)
        self.assertLess(parents, migration)
        self.assertNotIn("loop.index0", block)
        self.assertIn("topology_cascade_ingress_migration_index", block)
        self.assertIn("index_var: topology_cascade_ingress_migration_index", block)

    def test_ansible_tasks_do_not_use_jinja_loop_metadata(self):
        invalid_loop_metadata = re.compile(
            r"(?<![A-Za-z0-9_])loop\.(?:index0|index|first|last|revindex|length)\b"
        )
        paths = list((ROOT / "ansible" / "roles").rglob("tasks/*.yml"))
        paths.extend((ROOT / "ansible" / "playbooks").glob("*.yml"))
        for path in paths:
            match = invalid_loop_metadata.search(path.read_text(encoding="utf-8"))
            self.assertIsNone(match, str(path.relative_to(ROOT)))

    def test_cascade_tun_controller_keypair_does_not_require_controller_sudo(self):
        tasks = self.read("ansible/roles/transports/backhaul/ssh_tun/tasks/main.yml")
        include = tasks.index("Configure SSH TUN endpoint")
        client = tasks.index("Configure SSH TUN client endpoint")
        self.assertIn("apply:", tasks[include:client])
        self.assertIn("become: false", tasks[include:client])
        self.assertIn("ansible_connection: local", tasks[include:client])
        self.assertIn("ansible_remote_tmp: /tmp/ansible-local", tasks[include:client])

    def test_controller_local_tasks_use_explicit_local_connection_contract(self):
        files = (
            "ansible/roles/system_base/tasks/prepare_ssh_hardening.yml",
            "ansible/roles/topology/cascade/ingress/tasks/main.yml",
            "ansible/roles/transports/backhaul/ssh_tun/tasks/server.yml",
        )
        for relative in files:
            tasks = yaml.safe_load(self.read(relative))
            for task in tasks:
                if task.get("delegate_to") != "localhost":
                    continue
                task_vars = task.get("vars", {})
                self.assertFalse(task.get("become", True), relative)
                self.assertEqual(task_vars.get("ansible_connection"), "local", relative)
                self.assertEqual(
                    task_vars.get("ansible_remote_tmp"),
                    "/tmp/ansible-local",
                    relative,
                )

    def test_topology_cascade_ingress_does_not_manage_unbound(self):
        playbook = self.read("ansible/playbooks/deploy_cascade_ingress.yml")
        self.assertNotIn("system_base_docker_updater_manage_unbound", playbook)

    def test_cascade_dns_is_resolved_only_by_egress_unbound(self):
        logic = self.read("scripts/deployment_logic.py")
        template = self.read(
            "ansible/roles/topology/cascade/ingress/templates/xray/config.json.j2"
        )
        self.assertIn('"access_xray_dns_egress_only": True', logic)
        self.assertIn("{% if xray_dns_egress_only %}", template)
        self.assertIn('"finalQuery": true', template)
        self.assertIn('"https://dns.quad9.net/dns-query"', template)

    def test_cascade_country_policy_is_rendered_before_direct_proxy_fallbacks(self):
        template = self.read("ansible/roles/topology/cascade/ingress/templates/xray/config.json.j2")
        self.assertIn("access_xray_local_region_countries", template)
        self.assertIn('"geoip:{{ country }}"', template)
        self.assertLess(
            template.index("xray_local_region_countries | length > 0"),
            template.index('"ip": ["geoip:private"]'),
        )

    def test_cascade_xray_blocklist_is_always_rendered(self):
        template = self.read(
            "ansible/roles/topology/cascade/ingress/templates/xray/config.json.j2"
        )
        menu = self.read("lib/nodes.sh")
        self.assertIn(
            "{% set xray_block_domains = access_xray_block_domains | default([]) | unique | list %}",
            template,
        )
        self.assertNotIn("xray_blocking_enabled", template)
        self.assertNotIn("set-cascade-xray-blocking", menu)

    def test_topology_cascade_egress_installs_its_docker_updater(self):
        playbook = self.read("ansible/playbooks/deploy_cascade_egress.yml")
        self.assertIn("system_base_enable_docker_updater: true", playbook)
        self.assertIn(
            "system_base_docker_updater_name: \"{{ topology_cascade_egress_updater_name | default('cascade-egress') }}\"",
            playbook,
        )

    def test_topology_cascade_egress_bakes_the_repository_local_ad_rpz(self):
        tasks = self.read("ansible/roles/topology/cascade/egress/tasks/main.yml")
        dockerfile = self.read("ansible/roles/topology/cascade/egress/templates/dns/Dockerfile.j2")
        rpz_config = self.read(
            "ansible/roles/topology/cascade/egress/templates/dns/unbound.conf.d/40-rpz.conf.j2"
        )
        local_rpz = self.read(
            "ansible/roles/topology/cascade/egress/templates/dns/cascade-ads-tracking.rpz.j2"
        )

        self.assertIn("dns/rpz", tasks)
        self.assertIn("cascade-ads-tracking.rpz", tasks)
        self.assertIn(
            "COPY rpz/cascade-ads-tracking.rpz /var/lib/unbound/rpz/cascade-ads-tracking.rpz",
            dockerfile,
        )
        self.assertIn('source.name', rpz_config)
        self.assertIn('zonefile', rpz_config)
        self.assertIn("rpz-action-override: nxdomain", rpz_config)
        self.assertIn("an.yandex.ru CNAME .", local_rpz)
        self.assertIn("*.ads.mail.ru CNAME .", local_rpz)
        self.assertIn("$ORIGIN cascade-local-ads-tracking.", local_rpz)

    def test_topology_cascade_ingress_uses_main_release_resolution_contract(self):
        playbook = self.read("ansible/playbooks/deploy_cascade_ingress.yml")
        compose = self.read("ansible/roles/topology/cascade/ingress/templates/compose.yml.j2")
        updater = self.read("ansible/roles/system_base/templates/docker-project-updater.j2")
        self.assertIn("release_repo: Watfaq/clash-rs", playbook)
        self.assertIn("release_repo: XTLS/Xray-core", playbook)
        self.assertIn("release_image_template: ghcr.io/watfaq/clash-rs:latest", playbook)
        self.assertIn("release_image_template: ghcr.io/xtls/xray-core:latest", playbook)
        self.assertIn("image: local/cascade-clash-rs:latest", compose)
        self.assertIn("image: local/cascade-xray-core:auto", compose)
        self.assertIn("latest_semver_release", updater)
        self.assertIn("docker image tag", updater)

    def test_cascade_updater_schedules_match_original_nitka(self):
        egress = self.read("ansible/playbooks/deploy_cascade_egress.yml")
        ingress = self.read("ansible/playbooks/deploy_cascade_ingress.yml")
        docker_timer = self.read(
            "ansible/roles/system_base/templates/docker-project-updater.timer.j2"
        )
        os_timer = self.read(
            "ansible/roles/system_base/templates/nitka-os-updater.timer.j2"
        )

        self.assertIn('system_base_docker_updater_calendar: "*-*-* 02:00"', egress)
        self.assertIn('system_base_docker_updater_calendar: "*-*-* 02:20"', ingress)
        self.assertIn("RandomizedDelaySec={{ system_base_docker_updater_randomized_delay_sec }}", docker_timer)
        self.assertIn("AccuracySec=1min", docker_timer)
        self.assertIn("OnCalendar=*-*-* 01:00", os_timer)
        self.assertIn("RandomizedDelaySec=13m", os_timer)
        self.assertIn("AccuracySec=1h", os_timer)

    def test_full_removal_cleans_cascade_ingress_systemd_units(self):
        removal = self.read("ansible/playbooks/remove.yml")
        for unit in (
            "cascade-ingress-docker-updater.timer",
            "cascade-ingress-docker-updater.service",
            "cascade-os-updater.timer",
            "cascade-os-updater.service",
            "cascade-ingress-watchdog.timer",
            "cascade-ingress-watchdog.service",
        ):
            self.assertIn(unit, removal)
            self.assertIn(f"/etc/systemd/system/{unit}", removal)

    def test_topology_cascade_ingress_watchdog_and_health_timings_match_main(self):
        vars_file = self.read("ansible/roles/topology/cascade/ingress/vars/main.yml")
        defaults = self.read("ansible/roles/transports/backhaul/ssh_tun/defaults/main.yml")
        watchdog = self.read(
            "ansible/roles/topology/cascade/ingress/templates/cascade-ingress-watchdog.sh.j2"
        )
        compose = self.read("ansible/roles/topology/cascade/ingress/templates/compose.yml.j2")

        self.assertIn("topology_cascade_ingress_watchdog_interval_seconds: 5", vars_file)
        self.assertIn("backhaul_ssh_tun_client_healthcheck_start_period: 5s", defaults)
        self.assertIn("topology_cascade_ingress_clash_healthcheck_start_period: 90s", vars_file)
        self.assertIn('check_interval="{{ topology_cascade_ingress_watchdog_interval_seconds | default(30) }}"', watchdog)
        self.assertIn("start_period: {{ topology_cascade_ingress_clash_healthcheck_start_period | default('5s') }}", compose)

    def test_updater_scripts_preserve_lock_health_and_rollback_logic(self):
        current_docker = self.read(
            "ansible/roles/system_base/templates/docker-project-updater.j2"
        )
        current_os = self.read("ansible/roles/system_base/templates/nitka-os-updater.j2")
        golden_ref = os.environ.get("NITKA_GOLDEN_REF", "origin/main")
        original_os = subprocess.check_output(
            [
                "git",
                "show",
                f"{golden_ref}:tcp/roles/system_base/templates/nitka-os-updater.j2",
            ],
            cwd=ROOT,
            text=True,
        )
        original_os = original_os.replace(
            "/var/log/nitka-os-updater.log",
            "/var/log/{{ system_base_os_updater_name }}-os-updater.log",
        ).replace(
            "/run/nitka-os-updater.lock",
            "/run/{{ system_base_os_updater_name }}-os-updater.lock",
        )

        self.assertEqual(current_os, original_os)
        self.assertNotIn("awk", current_docker)
        for marker in (
            "flock -n 9",
            "wait_all_services",
            "rollback_to_backups",
            "docker image tag",
            "docker compose",
        ):
            self.assertIn(marker, current_docker)
        for marker in (
            "apt-get -qq -o Acquire::Retries=3 update",
            "systemctl reboot || /sbin/reboot",
        ):
            self.assertIn(marker, current_os)

        service = self.read("ansible/roles/system_base/templates/nitka-os-updater.service.j2")
        self.assertIn("TimeoutStartSec=2h", service)

    def test_cascade_tun_transport_is_distinct_from_container_ssh_port(self):
        compose = self.read("ansible/roles/topology/cascade/egress/templates/compose.yml.j2")
        client = self.read("ansible/roles/transports/backhaul/ssh_tun/templates/client/ssh_config.j2")
        known_hosts = self.read("ansible/roles/transports/backhaul/ssh_tun/templates/client/known_hosts.j2")
        self.assertIn(
            '"{{ backhaul_ssh_tun_external_port | default(backhaul_ssh_tun_ssh_port) }}:'
            '{{ backhaul_ssh_tun_internal_port | default(backhaul_ssh_tun_container_port) }}"',
            compose,
        )
        self.assertIn("Port {{ backhaul_ssh_tun_ssh_port }}", client)
        self.assertNotIn("Port 22", client)
        self.assertIn("backhaul_ssh_tun_host_public_key", known_hosts)
        self.assertIn("lookup('file', backhaul_ssh_tun_ssh_key_dir ~ '/ssh_host_ed25519_key.pub')", known_hosts)

    def test_cascade_runtime_consumes_backhaul_contract(self):
        ingress = self.read("ansible/roles/topology/cascade/ingress/templates/compose.yml.j2")
        ensure = self.read(
            "ansible/roles/topology/cascade/ingress/templates/ingress_stack_ensure.sh.j2"
        )
        watchdog = self.read(
            "ansible/roles/topology/cascade/ingress/templates/cascade-ingress-watchdog.sh.j2"
        )
        egress = self.read("ansible/roles/topology/cascade/egress/templates/compose.yml.j2")
        handler = self.read("ansible/roles/topology/cascade/egress/handlers/main.yml")

        for content in (ingress, ensure, watchdog, egress, handler):
            self.assertIn("backhaul_ssh_tun_service_name", content)
            self.assertIn("backhaul_ssh_tun_container_name", content)

        self.assertIn("backhaul_ssh_tun_healthcheck_interval", ingress)
        self.assertIn("backhaul_ssh_tun_startup_timeout", ensure)
        self.assertIn("backhaul_ssh_tun_healthcheck_start_period", egress)

    def test_cascade_updater_is_stopped_during_deployment(self):
        ingress = self.read("ansible/roles/topology/cascade/ingress/tasks/main.yml")
        egress = self.read("ansible/roles/topology/cascade/egress/handlers/main.yml")
        for content in (ingress, egress):
            self.assertNotIn("nitka-os-updater.timer", content)
            self.assertNotIn("/opt/nitka-egress", content)
            self.assertNotIn("/opt/nitka-ingress", content)
            self.assertNotIn("restore_legacy", content)
        self.assertIn("cascade-egress-docker-updater.timer", egress)
        self.assertIn("cascade-ingress-docker-updater.timer", ingress)

    def test_ingress_deployment_does_not_reclaim_unrelated_published_ports(self):
        ingress = self.read("ansible/roles/topology/cascade/ingress/tasks/main.yml")
        self.assertNotIn('docker ps -q --filter "publish=${port}"', ingress)

    def test_ingress_deployment_has_current_stack_backup_and_no_unprotected_handler_restart(self):
        ingress = self.read("ansible/roles/topology/cascade/ingress/tasks/main.yml")
        self.assertIn("cascade-ingress.latest", ingress)
        self.assertIn("Restore current Cascade ingress stack from backup", ingress)
        self.assertNotIn("restore_legacy", ingress.lower())
        self.assertNotIn("notify: Restart ingress stack", ingress)

    def test_cascade_creation_runs_one_automatic_install_pipeline(self):
        deployment = self.read("lib/deployment.sh")
        runtime = deployment[
            deployment.index("run_cascade_playbooks()") : deployment.index(
                "run_remove_with_management_key()"
            )
        ]
        add = deployment[
            deployment.index("add_cascade()") : deployment.index("deploy_node()")
        ]
        egress = runtime.index(
            'if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_cascade_egress.yml"; then\n            :'
        )
        capture = runtime.index('capture-cascade-transport-keys')
        ingress = runtime.index(
            'if run_ansible_playbook -i "$inventory" -e "@$extra" "$ROOT_DIR/ansible/playbooks/deploy_cascade_ingress.yml"; then\n            rc=0'
            , capture
        )
        self.assertLess(egress, capture)
        self.assertLess(capture, ingress)
        self.assertEqual(add.count('run_cascade_playbooks "$deployment_id" "$cascade_state"'), 1)

    def test_routing_update_only_deploys_ingress(self):
        nodes = self.read("lib/nodes.sh")
        self.assertIn(
            'run_cascade_ingress_playbook "$deployment_id" "$after"',
            nodes,
        )
        deployment = self.read("lib/deployment.sh")
        self.assertNotIn("topology_cascade_ingress_manage_ssh_tun=false", deployment)

    def test_cascade_ingress_probes_saved_management_endpoints_before_ansible(self):
        deployment = self.read("lib/deployment.sh")
        ingress = deployment[
            deployment.index("run_cascade_ingress_playbook()") : deployment.index(
                "run_node_playbook()"
            )
        ]
        self.assertIn("probe_ssh_endpoint", ingress)
        self.assertIn('"$port" "$management_sshd_port" "$bootstrap_port"', ingress)
        self.assertIn("pinned host key", ingress)
        self.assertIn("Cascade ingress management SSH is unavailable", ingress)

    def test_management_ssh_probe_requires_strict_pinned_host_key(self):
        ansible = self.read("lib/ansible.sh")
        probe = ansible[ansible.index("probe_ssh_endpoint()") : ansible.index("write_node_known_hosts()")]
        for option in (
            "-o IdentitiesOnly=yes",
            "-o BatchMode=yes",
            "-o StrictHostKeyChecking=yes",
            "-o UserKnownHostsFile=",
        ):
            self.assertIn(option, probe)

    def test_cascade_access_key_change_commits_vault_only_after_runtime_success(self):
        access_keys = self.read("lib/access_keys.sh")
        cascade = access_keys[
            access_keys.index("mutate_access_keys_and_deploy()") : access_keys.index(
                "add_access_keys_menu()"
            )
        ]
        runtime = cascade.index("run_cascade_ingress_playbook")
        commit = cascade.index("vault_save")
        success = cascade.index("pipeline_complete")
        rollback = cascade.index("access_keys_rollback")
        self.assertLess(runtime, commit)
        self.assertLess(commit, success)
        self.assertLess(commit, rollback)
        self.assertIn("The existing Vault was not changed.", cascade)

    def test_cascade_menu_uses_cascade_specific_policy_handlers(self):
        nodes = self.read("lib/nodes.sh")
        self.assertIn('manage_cascade_dns_protection "$deployment_id"', nodes)
        self.assertIn('manage_cascade_country_policy "$deployment_id"', nodes)

    def test_vpn_server_list_does_not_auto_open_single_item(self):
        nodes = (ROOT / "lib" / "nodes.sh").read_text()
        single_item_block = 'if [[ "$item_count" == 1 ]]; then'
        self.assertNotIn(single_item_block, nodes)
        self.assertNotIn(
            'manage_dns_protection "$(cascade_node "$deployment_id" egress)"',
            nodes,
        )
        self.assertNotIn(
            'manage_local_region_policy "$(cascade_node "$deployment_id" ingress)"',
            nodes,
        )

    def test_cascade_policy_handlers_use_scoped_playbook_modes(self):
        deployment = self.read("lib/deployment.sh")
        dns = self.read("lib/dns.sh")
        self.assertIn('"$deployment_id" "$after" egress-only', dns)
        self.assertIn('"$deployment_id" "$after" ingress-only', dns)
        self.assertIn('[[ "$mode" == egress-only ]]', deployment)
        self.assertIn('[[ "$mode" == ingress-only ]]', deployment)


if __name__ == "__main__":
    unittest.main()
