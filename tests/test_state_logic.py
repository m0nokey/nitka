import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.state_logic import (
    bot_port_pattern,
    build_port_mapping,
    generated_port,
    generated_vpn_ports,
)
from scripts.vault_schema import migrate_state


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_CLI = ROOT_DIR / "scripts" / "state_cli.py"


class PortGenerationTests(unittest.TestCase):
    def test_nat_is_derived_from_external_and_internal_ports(self):
        mapping = build_port_mapping(
            bootstrap_external=40001,
            management_external=40001,
            sshd_internal=22,
            bootstrap_internal=22,
            source="legacy_project_env",
            internal_source="inferred",
        )

        self.assertTrue(mapping["nat"]["enabled"])
        self.assertEqual(mapping["nat"]["detection"], "derived")
        self.assertEqual(mapping["ports"]["management_ssh"]["external"], 40001)
        self.assertEqual(mapping["ports"]["management_ssh"]["internal"], 22)

    def test_equal_ports_mean_no_translation_was_detected(self):
        mapping = build_port_mapping(
            bootstrap_external=40002,
            management_external=40002,
            sshd_internal=40002,
        )

        self.assertFalse(mapping["nat"]["enabled"])
        self.assertEqual(mapping["nat"]["confidence"], "high")

    def test_missing_internal_port_keeps_nat_unknown(self):
        mapping = build_port_mapping(
            bootstrap_external=22,
            management_external=22,
            sshd_internal=None,
            services={"xray_xhttp": 40003},
        )

        self.assertIsNone(mapping["nat"]["enabled"])
        self.assertEqual(mapping["nat"]["detection"], "unknown")
        self.assertIsNone(mapping["ports"]["xray_xhttp"]["external"])
        self.assertEqual(mapping["ports"]["xray_xhttp"]["external_status"], "not_recorded")

    def test_known_obvious_patterns_are_rejected(self):
        for port in (20000, 23456, 1212, 12321, 12312, 20202):
            with self.subTest(port=port):
                self.assertTrue(bot_port_pattern(port))

    def test_less_obvious_values_are_not_rejected_by_pattern_filter(self):
        for port in (24680, 13579, 31415):
            with self.subTest(port=port):
                self.assertFalse(bot_port_pattern(port))

    def test_generated_ports_meet_all_generation_constraints(self):
        used = set()
        ports = [generated_port(used) for _ in range(250)]

        self.assertEqual(len(ports), len(set(ports)))
        for port in ports:
            self.assertGreaterEqual(port, 20000)
            self.assertLessEqual(port, 60000)
            self.assertFalse(bot_port_pattern(port))
            digits = str(port)
            self.assertTrue(all(
                abs(int(left) - int(right)) >= 2
                for left, right in zip(digits, digits[1:])
            ))

    def test_default_mode_generates_both_ports(self):
        used = set()

        vision_port, xhttp_port = generated_vpn_ports(used)

        self.assertGreaterEqual(vision_port, 20000)
        self.assertLessEqual(vision_port, 60000)
        self.assertGreaterEqual(xhttp_port, 20000)
        self.assertLessEqual(xhttp_port, 60000)
        self.assertEqual(used, {vision_port, xhttp_port})

    def test_vision_443_mode_uses_https_port_and_generates_xhttp(self):
        used = set()

        vision_port, xhttp_port = generated_vpn_ports(used, "vision-443")

        self.assertEqual(vision_port, 443)
        self.assertGreaterEqual(xhttp_port, 20000)
        self.assertLessEqual(xhttp_port, 60000)
        self.assertEqual(used, {xhttp_port})

    def test_xhttp_443_mode_generates_vision_and_uses_https_port(self):
        used = set()

        vision_port, xhttp_port = generated_vpn_ports(used, "xhttp-443")

        self.assertGreaterEqual(vision_port, 20000)
        self.assertLessEqual(vision_port, 60000)
        self.assertEqual(xhttp_port, 443)
        self.assertEqual(used, {vision_port})

    def test_manual_mode_uses_the_requested_ports(self):
        used = {42137}

        ports = generated_vpn_ports(used, "manual", (443, 8443))

        self.assertEqual(ports, (443, 8443))
        self.assertEqual(used, {42137, 443, 8443})

    def test_manual_mode_rejects_invalid_port_pairs(self):
        for ports in ((443, 443), (0, 8443), (443, 65536), (42137, 8443)):
            with self.subTest(ports=ports):
                with self.assertRaises(ValueError):
                    generated_vpn_ports({42137}, "manual", ports)


class StateCliTests(unittest.TestCase):
    def run_cli(self, state, *arguments):
        state = migrate_state(state)
        result = subprocess.run(
            [sys.executable, str(STATE_CLI), *arguments],
            cwd=ROOT_DIR,
            input=json.dumps(state),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def fixture_state():
        return {
            "nodes": {
                "node-a": {
                    "name": "node-a",
                    "host": "192.0.2.10",
                    "management": {
                        "user": "deploy",
                        "authorized_key": "ssh-ed25519 AAAA old",
                        "private_key": "private-key",
                        "sshd_port": 42137,
                        "port": 22,
                    },
                    "bootstrap": {
                        "user": "root",
                        "private_key": "bootstrap-key",
                        "password": "bootstrap-password",
                        "port": 22,
                    },
                    "access": {
                        "transport": "xray-reality",
                        "xray_reality": {
                        "vision_port": 38642,
                        "xhttp_port": 49753,
                        "access_keys": [{
                            "key_id": "key-initial",
                            "vision_uuid": "11111111-1111-4111-8111-111111111111",
                            "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
                        }],
                        },
                        "ssh_proxy": {"access_keys": []},
                    },
                    "topology": {"role": "single"},
                },
            }
        }

    def test_key_lifecycle_and_dns_profile(self):
        state = self.fixture_state()
        state = self.run_cli(state, "add-keys", "node-a", "2")
        keys = state["nodes"]["node-a"]["access"]["xray_reality"]["access_keys"]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len({key["key_id"] for key in keys}), 3)

        state = self.run_cli(state, "remove-key", "node-a", keys[1]["key_id"])
        self.assertEqual(len(state["nodes"]["node-a"]["access"]["xray_reality"]["access_keys"]), 2)

        state = self.run_cli(
            state,
            "set-dns-profile",
            "node-a",
            "custom",
            "--dns-lists",
            "ads,malware",
        )
        xray = state["nodes"]["node-a"]["access"]["xray_reality"]
        self.assertEqual(xray["dns_filter_profile"], "custom")
        self.assertEqual(xray["dns_filter_lists"], ["ads", "malware"])

        state = self.run_cli(state, "remove-all-keys", "node-a")
        self.assertEqual(state["nodes"]["node-a"]["access"]["xray_reality"]["access_keys"], [])

    def test_ssh_proxy_access_keys_have_distinct_users_and_cannot_remove_last(self):
        state = self.fixture_state()
        node = state["nodes"]["node-a"]
        node["access"]["transport"] = "ssh-proxy"
        node["access"]["xray_reality"] = {}
        node["access"]["ssh_proxy"] = {
            "port": 30422,
            "access_keys": [{
                "key_id": "ssh-initial",
                "username": "abcdefgh1234",
                "private_key": "private-key",
                "authorized_key": "ssh-ed25519 AAAA old",
            }],
        }

        state = self.run_cli(state, "add-ssh-key", "node-a")
        keys = state["nodes"]["node-a"]["access"]["ssh_proxy"]["access_keys"]
        self.assertEqual(len(keys), 2)
        self.assertEqual(len({key["username"] for key in keys}), 2)
        self.assertTrue(all(re.fullmatch(r"[a-z][a-z0-9]{7,31}", key["username"]) for key in keys))
        self.assertTrue(all(key["private_key"] for key in keys))

        state = self.run_cli(state, "remove-ssh-key", "node-a", "ssh-initial")
        keys = state["nodes"]["node-a"]["access"]["ssh_proxy"]["access_keys"]
        self.assertEqual(len(keys), 1)

        result = subprocess.run(
            [sys.executable, str(STATE_CLI), "remove-ssh-key", "node-a", keys[0]["key_id"]],
            cwd=ROOT_DIR,
            input=json.dumps(state),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot delete the last SSH proxy access key", result.stderr)

    def test_switching_to_ssh_creates_and_persists_first_access_key(self):
        state = self.fixture_state()

        state = self.run_cli(state, "set-access-transport", "node-a", "ssh-proxy")

        transport = state["nodes"]["node-a"]["access"]["ssh_proxy"]
        self.assertEqual(len(transport["access_keys"]), 1)
        self.assertTrue(transport["access_keys"][0]["private_key"])
        self.assertTrue(transport["access_keys"][0]["authorized_key"].startswith("ssh-ed25519 "))
        self.assertGreater(transport["port"], 1024)
        info = self.run_cli(state, "ssh-transport-info", "node-a")
        self.assertTrue(info["access_keys"][0]["fingerprint"].startswith("SHA256:"))

    def test_ssh_proxy_host_fingerprint_is_separate_from_management_fingerprint(self):
        state = self.fixture_state()
        node = state["nodes"]["node-a"]
        node["access"]["transport"] = "ssh-proxy"
        node["access"]["xray_reality"] = {}
        node["access"]["ssh_proxy"] = {
            "port": 30422,
            "access_keys": [{
                "key_id": "ssh-initial",
                "username": "abcdefgh1234",
                "private_key": "private-key",
                "authorized_key": "ssh-ed25519 AAAA old",
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            key_file.write("ssh-ed25519 AAAA proxy")
            key_file.flush()
            state = self.run_cli(
                state,
                "set-ssh-transport-host-key",
                "node-a",
                key_file.name,
                "SHA256:proxyfingerprint",
            )

        transport = state["nodes"]["node-a"]["access"]["ssh_proxy"]
        self.assertEqual(transport["host_public_key"], "ssh-ed25519 AAAA proxy")
        self.assertEqual(transport["host_fingerprint"], "SHA256:proxyfingerprint")
        self.assertNotIn("ssh_host_fingerprint", transport)
        info = self.run_cli(state, "ssh-transport-info", "node-a")
        self.assertEqual(info["host_fingerprint"], "SHA256:proxyfingerprint")

    def test_extract_exposes_canonical_access_transport(self):
        result = subprocess.run(
            [sys.executable, str(STATE_CLI), "extract", "node-a"],
            cwd=ROOT_DIR,
            input=json.dumps(migrate_state(self.fixture_state())),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["access_transport_selection"], "xray-reality")

    def test_deployment_state_moves_to_management_port(self):
        state = self.run_cli(self.fixture_state(), "mark-deployed", "node-a")
        node = state["nodes"]["node-a"]
        self.assertEqual(node["bootstrap"]["private_key"], "")
        self.assertEqual(node["management"]["port"], node["management"]["sshd_port"])
        self.assertFalse(node["port_mapping"]["nat"]["enabled"])

    def test_management_user_can_be_normalized_to_deploy(self):
        state = self.fixture_state()
        state["nodes"]["node-a"]["management"]["user"] = "legacy-user"

        state = self.run_cli(state, "set-management-user", "node-a", "deploy")

        self.assertEqual(state["nodes"]["node-a"]["management"]["user"], "deploy")
        self.assertEqual(state["nodes"]["node-a"]["management"]["private_key"], "private-key")

    def test_deployment_state_preserves_detected_nat_mapping(self):
        state = self.fixture_state()
        state["nodes"]["node-a"]["port_mapping"] = {
            "nat": {"enabled": True},
            "ports": {"management_ssh": {"external": 40001}},
        }

        state = self.run_cli(state, "mark-deployed", "node-a")
        node = state["nodes"]["node-a"]

        self.assertEqual(node["management"]["port"], 40001)
        self.assertTrue(node["port_mapping"]["nat"]["enabled"])

    def test_ssh_mapping_records_the_verified_external_port(self):
        state = self.run_cli(self.fixture_state(), "set-ssh-mapping", "node-a", "40001", "40004")
        node = state["nodes"]["node-a"]

        self.assertEqual(node["management"]["port"], 40001)
        self.assertEqual(node["management"]["sshd_port"], 40004)
        self.assertTrue(node["port_mapping"]["nat"]["enabled"])

    def test_invalid_local_region_is_rejected(self):
        result = subprocess.run(
            [
                sys.executable,
                str(STATE_CLI),
                "set-local-region",
                "node-a",
                "enabled",
                "--local-region-countries",
                "xx",
            ],
            cwd=ROOT_DIR,
            input=json.dumps(migrate_state(self.fixture_state())),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported country code", result.stderr)

    def test_cascade_dns_and_country_policy_are_stored_in_deployment_settings(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "egress-private-key",
            "xray": {"access_keys": []},
        }
        state = self.run_cli(state, "add-cascade", "cascade-1", "node-a", "node-b")
        state = self.run_cli(
            state,
            "set-cascade-dns-profile",
            "cascade-1",
            "custom",
            "--dns-lists",
            "urlhaus,threatfox",
        )
        settings = state["deployments"]["cascade-1"]["settings"]
        self.assertEqual(
            [source["name"] for source in settings["topology_cascade_egress"]["rpz_sources"]],
            ["urlhaus", "threatfox"],
        )
        self.assertEqual(settings["topology_cascade_egress"]["rpz_profile"], "custom")
        state = self.run_cli(
            state,
            "set-cascade-dns-profile",
            "cascade-1",
            "custom",
            "--dns-lists",
            "cascade-local-ads-tracking",
        )
        self.assertEqual(
            [source["name"] for source in state["deployments"]["cascade-1"]["settings"]["topology_cascade_egress"]["rpz_sources"]],
            ["cascade-local-ads-tracking"],
        )
        state = self.run_cli(state, "set-cascade-dns-profile", "cascade-1", "maximum")
        settings = state["deployments"]["cascade-1"]["settings"]
        self.assertEqual(settings["topology_cascade_egress"]["rpz_profile"], "maximum")
        self.assertIn("hagezi-ultimate", [
            source["name"] for source in settings["topology_cascade_egress"]["rpz_sources"]
        ])
        state = self.run_cli(
            state,
            "set-cascade-country-policy",
            "cascade-1",
            "enabled",
            "--local-region-countries",
            "ru,de",
        )
        self.assertEqual(
            state["deployments"]["cascade-1"]["settings"]["topology_cascade_ingress"]["local_region_countries"],
            ["ru", "de"],
        )
    def test_add_cascade_links_existing_nodes_without_changing_credentials(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "egress-private-key",
            "xray": {"access_keys": []},
        }
        result = self.run_cli(
            state, "add-cascade", "cascade-1", "node-a", "node-b"
        )
        self.assertEqual(result["nodes"]["node-a"]["management"]["private_key"], "private-key")
        self.assertEqual(
            result["deployments"]["cascade-1"]["roles"]["ingress"]["node"],
            "node-a",
        )

    def test_replace_topology_cascade_egress_keeps_old_node_for_rollback(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "egress-private-key",
            "xray": {"access_keys": []},
        }
        state = self.run_cli(state, "add-cascade", "cascade-1", "node-a", "node-b")
        state["nodes"]["node-c"] = {
            "host": "192.0.2.30",
            "management_private_key": "new-egress-private-key",
            "xray": {"access_keys": []},
        }

        result = self.run_cli(
            state, "replace-cascade-node", "cascade-1", "egress", "node-c"
        )

        self.assertEqual(
            result["deployments"]["cascade-1"]["roles"]["egress"]["node"],
            "node-c",
        )
        self.assertIn("node-b", result["nodes"])

    def test_replace_topology_cascade_ingress_preserves_client_configuration(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "egress-private-key",
            "xray": {"access_keys": []},
        }
        state = self.run_cli(state, "add-cascade", "cascade-1", "node-a", "node-b")
        state["nodes"]["node-c"] = {
            "host": "192.0.2.30",
            "management_private_key": "new-ingress-private-key",
            "xray": {"access_keys": [{"key_id": "new-key"}]},
        }

        result = self.run_cli(
            state, "replace-cascade-node", "cascade-1", "ingress", "node-c"
        )

        self.assertEqual(result["nodes"]["node-c"]["access"]["xray_reality"], state["nodes"]["node-a"]["access"]["xray_reality"])

    def test_share_management_key_copies_only_management_credentials(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "other-private-key",
            "management_authorized_key": "ssh-ed25519 other",
            "xray": {"vision_port": 443},
        }
        result = self.run_cli(state, "share-management-key", "node-a", "node-b")

        self.assertEqual(
            result["nodes"]["node-b"]["management"]["private_key"],
            result["nodes"]["node-a"]["management"]["private_key"],
        )
        self.assertEqual(
            result["nodes"]["node-b"]["management"]["authorized_key"],
            result["nodes"]["node-a"]["management"]["authorized_key"],
        )
        self.assertEqual(result["nodes"]["node-b"]["access"]["xray_reality"], {"vision_port": 443})

    def test_add_cascade_rejects_missing_node(self):
        result = subprocess.run(
            [
                sys.executable,
                str(STATE_CLI),
                "add-cascade",
                "cascade-1",
                "node-a",
                "missing",
            ],
            cwd=ROOT_DIR,
            input=json.dumps(migrate_state(self.fixture_state())),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("egress node not found", result.stderr)

    def test_remove_cascade_removes_deployment_and_unshared_nodes(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "egress-private-key",
            "xray": {"access_keys": []},
        }
        state = self.run_cli(state, "add-cascade", "cascade-1", "node-a", "node-b")
        result = self.run_cli(state, "remove-cascade", "cascade-1")

        self.assertEqual(result.get("deployments"), {})
        self.assertNotIn("node-a", result["nodes"])
        self.assertNotIn("node-b", result["nodes"])

    def test_remove_node_rejects_nodes_still_used_by_a_cascade(self):
        state = self.fixture_state()
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_private_key": "egress-private-key",
            "xray": {"access_keys": []},
        }
        state = self.run_cli(state, "add-cascade", "cascade-1", "node-a", "node-b")
        result = subprocess.run(
            [sys.executable, str(STATE_CLI), "remove-node", "node-b"],
            cwd=ROOT_DIR,
            input=json.dumps(state),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("referenced by a Cascade", result.stderr)

    def test_extract_cascade_builds_ansible_variables(self):
        state = self.fixture_state()
        state["nodes"]["node-a"]["access"]["xray_reality"].update({
            "reality_private_key": "private-reality-key",
            "reality_public_key": "public-reality-key",
            "reality_short_id": "0123456789abcdef",
            "server_name": "example.com",
        })
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_authorized_key": "ssh-ed25519 egress",
            "xray": {"access_keys": []},
        }
        state = self.run_cli(state, "add-cascade", "cascade-1", "node-a", "node-b")

        variables = self.run_cli(
            state,
            "--cascade-local-root",
            "/state/nitka/cascade",
            "extract-cascade",
            "cascade-1",
        )

        self.assertEqual(variables["topology_cascade_ingress_local_dir"], "/state/nitka/cascade/cascade-1/ingress")
        self.assertEqual(variables["topology_cascade_egress_deploy_authorized_key"], "ssh-ed25519 egress")

    def test_extract_cascade_migrates_partial_legacy_deployment(self):
        state = self.fixture_state()
        state["nodes"]["node-a"]["access"]["xray_reality"].update({
            "reality_private_key": "private-reality-key",
            "reality_public_key": "public-reality-key",
            "reality_short_id": "0123456789abcdef",
            "server_name": "example.com",
        })
        state["nodes"]["node-b"] = {
            "host": "192.0.2.20",
            "management_authorized_key": "ssh-ed25519 egress",
            "xray": {"access_keys": []},
        }
        state["deployments"] = {
            "cascade-legacy": {
                "topology": "cascade",
                "roles": {
                    "ingress": {"node": "node-a"},
                    "egress": {"node": "node-b"},
                },
                "settings": {"ssh_tun": {"port": 31847}},
                "services": {"ssh_tun": {"enabled": True}},
            }
        }

        result = subprocess.run(
            [
                sys.executable,
                str(STATE_CLI),
                "--cascade-local-root",
                "/state/nitka/cascade",
                "extract-cascade",
                "cascade-legacy",
            ],
            cwd=ROOT_DIR,
            input=json.dumps(migrate_state(state)),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        variables = json.loads(result.stdout)
        self.assertEqual(
            variables["topology_cascade_ingress_access_transport"],
            "xray-reality",
        )
        self.assertEqual(variables["backhaul_ssh_tun_ssh_port"], 31847)

    def test_pending_install_can_resume_and_commit_without_exposing_node(self):
        transaction_id = "install-test-1"
        pending = self.run_cli(
            self.fixture_state(), "begin-install", "node-a", transaction_id
        )

        self.assertNotIn("node-a", pending["nodes"])
        self.assertEqual(
            pending["pending_operations"][transaction_id]["phase"], "prepared"
        )

        candidate = self.run_cli(pending, "restore-pending", transaction_id)
        candidate["nodes"]["node-a"]["test_phase"] = "service-failed"
        pending = self.run_cli(candidate, "sync-pending", transaction_id, "service-failed")

        self.assertNotIn("node-a", pending["nodes"])
        self.assertEqual(
            pending["pending_operations"][transaction_id]["node"]["test_phase"],
            "service-failed",
        )

        committed = self.run_cli(pending, "commit-pending", transaction_id)
        self.assertIn("node-a", committed["nodes"])
        self.assertNotIn(transaction_id, committed["pending_operations"])

    def test_pending_install_abort_removes_only_the_transaction(self):
        transaction_id = "install-test-2"
        pending = self.run_cli(
            self.fixture_state(), "begin-install", "node-a", transaction_id
        )
        aborted = self.run_cli(pending, "abort-pending", transaction_id)

        self.assertEqual(aborted["nodes"], {})
        self.assertEqual(aborted["pending_operations"], {})


if __name__ == "__main__":
    unittest.main()
