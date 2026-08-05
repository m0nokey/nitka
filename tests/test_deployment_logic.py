import copy
import unittest

from scripts.vault_schema import migrate_state
from scripts.deployment_logic import (
    attach_cascade,
    cascade_ansible_vars,
    cascade_deployment,
    deployment_transport_summary,
    set_cascade_transports,
    validate_deployments,
)


class DeploymentLogicTests(unittest.TestCase):
    @staticmethod
    def state():
        return {
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management": {"user": "deploy", "authorized_key": "ssh-ed25519 ingress"},
                    "bootstrap": {"user": "root"},
                    "access": {"transport": "xray-reality", "xray_reality": {"access_keys": []}, "ssh_proxy": {}},
                    "topology": {"role": "single"},
                },
                "egress-node": {
                    "host": "192.0.2.20",
                    "management": {"user": "deploy", "authorized_key": "ssh-ed25519 egress"},
                    "bootstrap": {"user": "root"},
                    "access": {"transport": "xray-reality", "xray_reality": {"access_keys": []}, "ssh_proxy": {}},
                    "topology": {"role": "single"},
                },
            }
        }

    def test_cascade_uses_current_xray_adapter_for_both_roles(self):
        deployment = cascade_deployment("cascade-1", "ingress-node", "egress-node")

        self.assertEqual(deployment["topology"], "cascade")
        self.assertEqual(
            deployment["transports"],
            {
                "access": {"transport": "xray-reality"},
                "backhaul": {"transport": "ssh-tun"},
            },
        )
        self.assertEqual(deployment["roles"], {
            "ingress": {"node": "ingress-node"},
            "egress": {"node": "egress-node"},
        })
        self.assertTrue(deployment["services"]["backhaul_ssh_tun"]["enabled"])
        self.assertNotEqual(deployment["settings"]["backhaul_ssh_tun"]["port"], 22)
        self.assertGreaterEqual(deployment["settings"]["backhaul_ssh_tun"]["port"], 20000)
        self.assertLessEqual(deployment["settings"]["backhaul_ssh_tun"]["port"], 60000)

    def test_transport_summary_keeps_access_and_backhaul_independent(self):
        state = self.state()
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        summary = deployment_transport_summary(state, "cascade-1")

        self.assertEqual(summary["access"]["transport"], "xray-reality")
        self.assertTrue(summary["access"]["implemented"])
        self.assertEqual(summary["backhaul"]["transport"], "ssh-tun")
        self.assertTrue(summary["backhaul"]["implemented"])

    def test_transport_migration_is_validated_and_keeps_other_deployment_data(self):
        state = self.state()
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")
        original_roles = copy.deepcopy(state["deployments"]["cascade-1"]["roles"])

        migrated = set_cascade_transports(
            state, "cascade-1", "xray-reality", "ssh-tun"
        )

        self.assertEqual(
            migrated["deployments"]["cascade-1"]["transports"],
            {
                "access": {"transport": "xray-reality"},
                "backhaul": {"transport": "ssh-tun"},
            },
        )
        self.assertEqual(migrated["deployments"]["cascade-1"]["roles"], original_roles)
        with self.assertRaisesRegex(ValueError, "not implemented"):
            set_cascade_transports(state, "cascade-1", "naiveproxy", "ssh-tun")

    def test_cascade_replaces_invalid_tun_transport_automatically(self):
        state = {
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management_user": "deploy",
                    "management_authorized_key": "ssh-ed25519 ingress",
                    "xray": {
                        "vision_port": 443,
                        "xhttp_port": 23457,
                        "reality_private_key": "private-reality-key",
                        "reality_public_key": "public-reality-key",
                        "reality_short_id": "0123456789abcdef",
                        "server_name": "example.com",
                        "access_keys": [{
                            "vision_uuid": "11111111-1111-4111-8111-111111111111",
                            "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
                        }],
                    },
                },
                "egress-node": {
                    "host": "192.0.2.20",
                    "management_user": "deploy",
                    "management_authorized_key": "ssh-ed25519 egress",
                },
            },
        }
        state = migrate_state(state)
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")
        state["deployments"]["cascade-1"]["settings"]["backhaul_ssh_tun"]["port"] = 22

        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")
        self.assertNotEqual(variables["backhaul_ssh_tun_ssh_port"], 22)
        self.assertGreaterEqual(variables["backhaul_ssh_tun_ssh_port"], 1025)
        self.assertLessEqual(variables["backhaul_ssh_tun_ssh_port"], 65535)

        state["deployments"]["cascade-1"]["settings"]["backhaul_ssh_tun"]["port"] = 31847
        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")
        self.assertEqual(variables["backhaul_ssh_tun_ssh_port"], 31847)

    def test_attach_preserves_nodes_and_does_not_mutate_input(self):
        state = self.state()
        original = copy.deepcopy(state)

        result = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        self.assertEqual(state, original)
        self.assertEqual(set(result["nodes"]), set(state["nodes"]))
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(
            result["deployments"]["cascade-1"]["roles"]["ingress"]["node"],
            "ingress-node",
        )
        self.assertTrue(validate_deployments(result))

    def test_cascade_requires_two_existing_nodes(self):
        with self.assertRaisesRegex(ValueError, "egress node not found"):
            attach_cascade(self.state(), "cascade-1", "ingress-node", "missing")

        with self.assertRaisesRegex(ValueError, "different nodes"):
            attach_cascade(self.state(), "cascade-1", "ingress-node", "ingress-node")

    def test_validate_rejects_unknown_role_node(self):
        state = self.state()
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")
        state["deployments"]["cascade-1"]["roles"]["egress"]["node"] = "missing"

        with self.assertRaisesRegex(ValueError, "deployment node not found"):
            validate_deployments(state)

    def test_cascade_ansible_vars_map_existing_xray_state(self):
        state = {
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management_user": "legacy-ingress-user",
                    "bootstrap_user": "legacy-bootstrap",
                    "management_authorized_key": "ssh-ed25519 legacy-ingress",
                    "xray": {
                        "vision_port": 443,
                        "xhttp_port": 23457,
                        "reality_private_key": "private-reality-key",
                        "reality_public_key": "public-reality-key",
                        "reality_short_id": "0123456789abcdef",
                        "server_name": "example.com",
                        "access_keys": [
                            {
                                "key_id": "key-one",
                                "vision_uuid": "11111111-1111-4111-8111-111111111111",
                                "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
                            },
                            {
                                "key_id": "key-two",
                                "vision_uuid": "33333333-3333-4333-8333-333333333333",
                                "xhttp_uuid": "44444444-4444-4444-8444-444444444444",
                            },
                        ],
                    },
                },
                "egress-node": {
                    "host": "192.0.2.20",
                    "management_user": "legacy-egress-user",
                    "bootstrap_user": "root",
                    "management_authorized_key": "ssh-ed25519 legacy-egress",
                },
            }
        }
        state = migrate_state(state)
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")

        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")

        self.assertEqual(variables["system_base_deploy_user"], "deploy")
        self.assertEqual(
            variables["topology_cascade_ingress_deploy_authorized_key"],
            "ssh-ed25519 legacy-ingress",
        )
        self.assertEqual(
            variables["topology_cascade_egress_remote_dir"],
            "/opt/nitka/cascade/egress",
        )
        self.assertEqual(
            variables["access_xray_reality_uuid"],
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(
            [key["key_id"] for key in variables["access_xray_access_keys"]],
            ["key-one", "key-two"],
        )
        self.assertEqual(
            variables["backhaul_ssh_tun_public_host"],
            "192.0.2.20",
        )
        self.assertEqual(
            variables["topology_cascade_ingress_access_transport"], "xray-reality"
        )
        self.assertEqual(
            variables["topology_cascade_ingress_backhaul_transport"], "ssh-tun"
        )
        self.assertEqual(
            variables["topology_cascade_egress_backhaul_transport"], "ssh-tun"
        )
        self.assertEqual(
            variables["topology_cascade_transport_bindings"][1]["endpoint"], "client"
        )
        self.assertEqual(
            variables["backhaul_ssh_tun_client_service_name"], "ssh_tun_client"
        )
        self.assertEqual(
            variables["backhaul_ssh_tun_server_service_name"], "ssh_tun_server"
        )
        self.assertEqual(
            variables["topology_cascade_ingress_harden_ssh_preserve_users"],
            ["legacy-ingress-user", "legacy-bootstrap"],
        )
        self.assertEqual(
            variables["access_xray_local_region_countries"], []
        )
        self.assertEqual(
            variables["topology_cascade_egress_harden_ssh_preserve_users"],
            ["legacy-egress-user"],
        )
        self.assertEqual(
            [source["name"] for source in variables["topology_cascade_egress_unbound_rpz_sources"]],
            ["urlhaus", "hagezi-tif-mini", "threatfox", "hagezi-dyndns"],
        )

    def test_cascade_maps_backhaul_port_and_separate_keys(self):
        state = {
            "nodes": {
                "ingress-node": {
                    "host": "192.0.2.10",
                    "management_user": "deploy",
                    "management_authorized_key": "ssh-ed25519 ingress",
                    "xray": {
                        "vision_port": 443,
                        "xhttp_port": 23457,
                        "reality_private_key": "private-reality-key",
                        "reality_public_key": "public-reality-key",
                        "reality_short_id": "0123456789abcdef",
                        "server_name": "example.com",
                        "access_keys": [{
                            "vision_uuid": "11111111-1111-4111-8111-111111111111",
                            "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
                        }],
                    },
                },
                "egress-node": {
                    "host": "192.0.2.20",
                    "management_user": "deploy",
                    "management_authorized_key": "ssh-ed25519 egress",
                },
            },
        }
        state = migrate_state(state)
        state = attach_cascade(state, "cascade-1", "ingress-node", "egress-node")
        state["deployments"]["cascade-1"]["settings"]["backhaul_ssh_tun"].update({
            "port": 31847,
            "auth_private_key": "-----BEGIN OPENSSH PRIVATE KEY----- auth",
            "auth_public_key": "ssh-ed25519 AAAA auth",
            "host_private_key": "-----BEGIN OPENSSH PRIVATE KEY----- host",
            "host_public_key": "ssh-ed25519 AAAA host",
        })

        variables = cascade_ansible_vars(state, "cascade-1", "/state/nitka/cascade")

        self.assertEqual(variables["backhaul_ssh_tun_ssh_port"], 31847)
        self.assertEqual(variables["backhaul_ssh_tun_container_port"], 22)
        self.assertEqual(
            variables["backhaul_ssh_tun_auth_private_key"],
            "-----BEGIN OPENSSH PRIVATE KEY----- auth",
        )
        self.assertEqual(
            variables["backhaul_ssh_tun_host_private_key"],
            "-----BEGIN OPENSSH PRIVATE KEY----- host",
        )
        self.assertEqual(
            variables["backhaul_ssh_tun_host_public_key"],
            "ssh-ed25519 AAAA host",
        )

    def test_legacy_cascade_without_transport_block_renders_after_migration(self):
        state = self.state()
        state["nodes"]["ingress-node"]["access"]["xray_reality"].update({
            "vision_port": 443,
            "xhttp_port": 23457,
            "reality_private_key": "private-reality-key",
            "reality_public_key": "public-reality-key",
            "reality_short_id": "0123456789abcdef",
            "server_name": "example.com",
            "access_keys": [{
                "vision_uuid": "11111111-1111-4111-8111-111111111111",
                "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
            }],
        })
        state["deployments"] = {
            "cascade-legacy": {
                "topology": "cascade",
                "roles": {
                    "ingress": {"node": "ingress-node"},
                    "egress": {"node": "egress-node"},
                },
                "settings": {"ssh_tun": {"port": 31847}},
                "services": {"ssh_tun": {"enabled": True}},
            }
        }

        migrated = migrate_state(state)
        variables = cascade_ansible_vars(
            migrated, "cascade-legacy", "/state/nitka/cascade"
        )

        self.assertEqual(
            variables["topology_cascade_ingress_access_transport"],
            "xray-reality",
        )
        self.assertEqual(
            variables["topology_cascade_ingress_backhaul_transport"],
            "ssh-tun",
        )
        self.assertEqual(variables["backhaul_ssh_tun_ssh_port"], 31847)


if __name__ == "__main__":
    unittest.main()
