from pathlib import Path
import unittest

import yaml

from scripts.transport_registry import (
    ACCESS_ADAPTERS,
    BACKHAUL_ADAPTERS,
    TRANSPORT_OPERATIONS,
    adapter_lifecycle,
)


ROOT = Path(__file__).resolve().parents[1]


class TransportLifecycleContractTests(unittest.TestCase):
    def implemented_adapters(self):
        for registry in (ACCESS_ADAPTERS, BACKHAUL_ADAPTERS):
            yield from (adapter for adapter in registry.values() if adapter.implemented)

    def role_path(self, adapter):
        return ROOT / "ansible/roles" / adapter.implementation_role

    def load_tasks(self, path):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertIsInstance(document, list, path)
        return document

    @staticmethod
    def task_text(tasks):
        return repr(tasks)

    def test_every_implemented_adapter_has_every_lifecycle_entrypoint(self):
        for adapter in self.implemented_adapters():
            role = self.role_path(adapter)
            self.assertTrue(role.is_dir(), adapter.implementation_role)
            self.assertTrue((role / "defaults/main.yml").exists(), adapter.name)
            for operation in TRANSPORT_OPERATIONS:
                task_file = role / "tasks" / f"{operation}.yml"
                self.assertTrue(task_file.exists(), f"{adapter.name}: {task_file}")
                self.assertTrue(self.load_tasks(task_file), f"empty {task_file}")

    def test_deploy_entrypoint_is_explicit_and_delegates_to_adapter_implementation(self):
        for adapter in self.implemented_adapters():
            tasks = self.load_tasks(self.role_path(adapter) / "tasks/deploy.yml")
            text = self.task_text(tasks)
            self.assertIn("main.yml", text, adapter.name)
            self.assertNotIn("deploy.yml", text.replace("tasks/deploy.yml", ""), adapter.name)

    def test_verify_entrypoint_checks_container_health(self):
        for adapter in self.implemented_adapters():
            tasks = self.load_tasks(self.role_path(adapter) / "tasks/verify.yml")
            text = self.task_text(tasks)
            self.assertIn("docker_container_info", text, adapter.name)
            self.assertIn("Health.Status", text, adapter.name)
            self.assertIn("healthy", text, adapter.name)

    def test_restart_entrypoint_restarts_and_rechecks_container(self):
        for adapter in self.implemented_adapters():
            tasks = self.load_tasks(self.role_path(adapter) / "tasks/restart.yml")
            text = self.task_text(tasks)
            self.assertIn("force-recreate", text, adapter.name)
            self.assertIn("docker_container_info", text, adapter.name)
            self.assertIn("healthy", text, adapter.name)

    def test_install_rollback_delegates_to_complete_remove(self):
        for adapter in self.implemented_adapters():
            tasks = self.load_tasks(
                self.role_path(adapter) / "tasks/rollback_install.yml"
            )
            text = self.task_text(tasks)
            self.assertIn("remove.yml", text, adapter.name)

    def test_update_rollback_reapplies_previous_adapter_configuration(self):
        for adapter in self.implemented_adapters():
            tasks = self.load_tasks(
                self.role_path(adapter) / "tasks/rollback_update.yml"
            )
            text = self.task_text(tasks)
            self.assertIn("main.yml", text, adapter.name)

    def test_remove_entrypoint_removes_runtime_state(self):
        for adapter in self.implemented_adapters():
            tasks = self.load_tasks(self.role_path(adapter) / "tasks/remove.yml")
            text = self.task_text(tasks)
            self.assertIn("'state': 'absent'", text, adapter.name)


if __name__ == "__main__":
    unittest.main()
