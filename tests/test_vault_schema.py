import copy
import unittest

from scripts.vault_schema import (
    DEFAULT_CASCADE_ACCESS_TRANSPORT,
    DEFAULT_CASCADE_BACKHAUL_TRANSPORT,
    VAULT_SCHEMA_VERSION,
    migrate_state,
    sync_canonical_state,
)


class VaultSchemaTests(unittest.TestCase):
    def test_legacy_node_is_migrated_to_namespaced_blocks(self):
        state = {
            "nodes": {
                "node-a": {
                    "management_user": "deploy",
                    "management_private_key": "private",
                    "management_authorized_key": "public",
                    "ssh_port": 37140,
                    "management_port": 37140,
                    "bootstrap_user": "root",
                    "bootstrap_ssh_port": 22,
                    "access_transport": "ssh",
                    "xray": {},
                    "ssh_transport": {"port": 53824},
                    "role": "single",
                }
            }
        }

        migrated = migrate_state(state)
        node = migrated["nodes"]["node-a"]
        self.assertEqual(migrated["vault_schema_version"], VAULT_SCHEMA_VERSION)
        self.assertEqual(node["management"]["port"], 37140)
        self.assertEqual(node["bootstrap"]["port"], 22)
        self.assertEqual(node["access"]["transport"], "ssh-proxy")
        self.assertEqual(node["access"]["ssh_proxy"]["port"], 53824)
        self.assertEqual(node["topology"]["role"], "single")
        self.assertEqual(node["management"]["user"], "deploy")
        self.assertFalse({
            "management_user", "management_private_key", "management_port",
            "bootstrap_user", "bootstrap_ssh_port", "access_transport",
            "xray", "ssh_transport", "role",
        } & node.keys())

    def test_migration_is_idempotent_and_sync_preserves_canonical_writers(self):
        state = {"nodes": {"node-a": {"management_user": "deploy", "xray": {}}}}
        migrate_state(state)
        state["nodes"]["node-a"]["management"]["user"] = "operator"
        sync_canonical_state(state)
        sync_canonical_state(state)
        self.assertEqual(state["nodes"]["node-a"]["management"]["user"], "operator")

    def test_canonical_only_node_stays_canonical(self):
        state = {
            "nodes": {
                "node-a": {
                    "host": "203.0.113.10",
                    "management": {"user": "deploy", "port": 38251},
                    "bootstrap": {"user": "root", "port": 22},
                    "access": {
                        "transport": "ssh-proxy",
                        "ssh_proxy": {"access_keys": [{"username": "abc12345"}]},
                    },
                    "topology": {"role": "standalone"},
                }
            },
            "pending_operations": {},
        }

        node = migrate_state(state)["nodes"]["node-a"]

        self.assertEqual(node["management"]["port"], 38251)
        self.assertEqual(node["bootstrap"]["user"], "root")
        self.assertEqual(node["access"]["transport"], "ssh-proxy")
        self.assertEqual(node["access"]["ssh_proxy"]["access_keys"][0]["username"], "abc12345")
        self.assertEqual(node["topology"]["role"], "standalone")
        self.assertFalse(set(node) & {
            "management_port", "bootstrap_user", "access_transport",
            "ssh_transport", "role",
        })

    def test_legacy_single_ssh_proxy_key_is_migrated_to_access_keys(self):
        state = {"nodes": {"node-a": {
            "ssh_transport": {
                "port": 53824,
                "username": "abc12345",
                "private_key": "private",
                "authorized_key": "ssh-ed25519 AAAA",
            }
        }}}

        node = migrate_state(state)["nodes"]["node-a"]
        self.assertEqual(node["access"]["ssh_proxy"]["access_keys"][0]["key_id"], "ssh-key-legacy")
        self.assertEqual(node["access"]["ssh_proxy"]["access_keys"][0]["username"], "abc12345")
        self.assertNotIn("private_key", node["access"]["ssh_proxy"])

    def test_legacy_xray_keys_receive_stable_share_ids(self):
        state = {"nodes": {"node-a": {
            "xray": {"access_keys": [{
                "key_id": "key-one",
                "vision_uuid": "11111111-1111-4111-8111-111111111111",
                "xhttp_uuid": "22222222-2222-4222-8222-222222222222",
            }]}
        }}}

        migrated = migrate_state(state)
        share_id = migrated["nodes"]["node-a"]["access"]["xray_reality"][
            "access_keys"
        ][0]["share_id"]
        self.assertRegex(share_id, r"^k[a-z0-9]{6}$")
        self.assertEqual(migrate_state(state), migrated)

    def test_canonical_xray_key_without_share_id_is_rejected_until_migration(self):
        state = {"nodes": {"node-a": {
            "access": {"xray_reality": {"access_keys": [{"key_id": "key-one"}]}},
        }}}
        migrated = migrate_state(state)
        sync_canonical_state(migrated)
        self.assertIn("share_id", migrated["nodes"]["node-a"]["access"]["xray_reality"]["access_keys"][0])

    def test_duplicate_xray_share_ids_are_rejected(self):
        state = {
            "vault_schema_version": VAULT_SCHEMA_VERSION,
            "nodes": {"node-a": {
                "management": {},
                "bootstrap": {},
                "access": {"xray_reality": {"access_keys": [
                    {"key_id": "one", "share_id": "k8m4q2p"},
                    {"key_id": "two", "share_id": "k8m4q2p"},
                ]}},
                "topology": {},
            }},
        }
        with self.assertRaisesRegex(ValueError, "duplicate share_id"):
            sync_canonical_state(state)

    def test_partial_cascade_gets_the_canonical_transport_plan(self):
        state = {
            "nodes": {},
            "deployments": {
                "cascade-legacy": {
                    "topology": "cascade",
                    "roles": {
                        "ingress": {"node": "ingress"},
                        "egress": {"node": "egress"},
                    },
                    "settings": {"ssh_tun": {"port": 31847}},
                    "services": {"ssh_tun": {"enabled": True}, "dns": {"enabled": True}},
                }
            },
        }

        migrated = migrate_state(state)
        deployment = migrated["deployments"]["cascade-legacy"]

        self.assertEqual(
            deployment["transports"],
            {
                "access": {"transport": DEFAULT_CASCADE_ACCESS_TRANSPORT},
                "backhaul": {"transport": DEFAULT_CASCADE_BACKHAUL_TRANSPORT},
            },
        )
        self.assertEqual(deployment["settings"]["backhaul_ssh_tun"]["port"], 31847)
        self.assertIn("backhaul_ssh_tun", deployment["services"])
        sync_canonical_state(migrated)

    def test_legacy_cascade_transport_fields_are_migrated_and_removed(self):
        state = {
            "nodes": {},
            "deployments": {
                "cascade-legacy": {
                    "roles": {
                        "ingress": {"node": "ingress"},
                        "egress": {"node": "egress"},
                    },
                    "access_transport": "existing-xray",
                    "backhaul_transport": "ssh-tun",
                }
            },
        }

        migrated = migrate_state(state)
        deployment = migrated["deployments"]["cascade-legacy"]

        self.assertEqual(deployment["topology"], "cascade")
        self.assertEqual(deployment["transports"]["access"]["transport"], "xray-reality")
        self.assertEqual(deployment["transports"]["backhaul"]["transport"], "ssh-tun")
        self.assertNotIn("access_transport", deployment)
        self.assertNotIn("backhaul_transport", deployment)

    def test_migration_of_cascade_transport_plan_is_idempotent(self):
        state = {
            "nodes": {},
            "deployments": {
                "cascade-1": {
                    "topology": "cascade",
                    "roles": {
                        "ingress": {"node": "ingress"},
                        "egress": {"node": "egress"},
                    },
                }
            },
        }

        first = migrate_state(state)
        snapshot = copy.deepcopy(first)
        second = migrate_state(first)

        self.assertEqual(second, snapshot)

    def test_canonical_cascade_without_transports_is_rejected(self):
        state = {
            "vault_schema_version": VAULT_SCHEMA_VERSION,
            "nodes": {},
            "deployments": {
                "cascade-1": {
                    "topology": "cascade",
                    "roles": {
                        "ingress": {"node": "ingress"},
                        "egress": {"node": "egress"},
                    },
                    "settings": {},
                    "services": {},
                }
            },
        }

        with self.assertRaisesRegex(ValueError, "transports object"):
            sync_canonical_state(state)

    def test_canonical_cascade_with_unimplemented_transport_is_rejected(self):
        state = {
            "vault_schema_version": VAULT_SCHEMA_VERSION,
            "nodes": {},
            "deployments": {
                "cascade-1": {
                    "topology": "cascade",
                    "roles": {
                        "ingress": {"node": "ingress"},
                        "egress": {"node": "egress"},
                    },
                    "transports": {
                        "access": {"transport": "naiveproxy"},
                        "backhaul": {"transport": "ssh-tun"},
                    },
                    "settings": {},
                    "services": {},
                }
            },
        }

        with self.assertRaisesRegex(ValueError, "not implemented"):
            sync_canonical_state(state)


if __name__ == "__main__":
    unittest.main()
