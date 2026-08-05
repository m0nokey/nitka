# Adding a Transport

This document is the extension contract. A new transport is complete only
when every item below is implemented and tested.

## 1. Choose the plane and identifier

Use one lowercase identifier containing only letters, digits, and hyphens.
Choose exactly one plane:

```text
access   client device -> ingress VPS
backhaul ingress VPS -> egress VPS
```

Do not create a combined name such as `cascade_<transport>`. Topology and
transport are separate dimensions.

## 2. Create the adapter role

Create one directory under `ansible/roles/transports/access/` or
`ansible/roles/transports/backhaul/`. The role must contain:

```text
defaults/main.yml
tasks/main.yml
tasks/verify.yml
tasks/rollback_install.yml
tasks/rollback_update.yml
tasks/restart.yml
tasks/remove.yml
templates/
handlers/main.yml
README.md
```

`tasks/deploy.yml` is the deploy operation. It may delegate the implementation
to `tasks/main.yml`, but callers must always use the explicit lifecycle name.
Every variable in the role begins with `access_<transport_id>_` or
`backhaul_<transport_id>_`.

## 3. Define the runtime contract

The role must document and implement required inputs, generated credentials,
the application directory, Compose project, service and container names, the
internal endpoint consumed by topology or routing, healthchecks, external
reachability, partial-install cleanup, update rollback, and complete removal.

Backhaul adapters must publish a topology runtime contract instead of making
topology templates guess service names or ports. Access adapters must publish
their client-facing endpoint and capabilities.

## 4. Register the adapter

Add one `TransportAdapter` entry in `scripts/transport_registry.py`:

```python
TransportAdapter(
    name="example",
    plane=PLANE_ACCESS,
    implementation_role="transports/access/example",
    supported_topologies=(TOPOLOGY_STANDALONE,),
    implemented=True,
)
```

The registry is the only place that defines whether the adapter is selectable,
which topologies it supports, and where its implementation role lives. Do not
edit an existing adapter's implementation to add a new one.

## 5. Add UI metadata, not UI logic

The menu may add a display label, capability summary, and help text. The UI
must pass the canonical transport identifier to the generic pipeline. It must
not know Docker commands, Ansible task names, or transport credentials.

## 6. Add Vault state

Store configuration and credentials under the transport namespace. Do not add
new top-level fields such as `ssh_port`, `xray_key`, or `proxy_user`.

For an existing Vault format, extend the one-time migration in
`scripts/vault_schema.py` and cover it through `scripts/migrate_vault.py`:

- migrations must be idempotent;
- runtime must not keep a legacy compatibility view;
- the original encrypted Vault must be backed up before rewrite;
- migration must be tested with old, current, and partially populated state;
- no private key may be logged or printed during migration.

## 7. Add pipeline phases and diagnostics

Use the common phases and add adapter-specific status IDs only under the
adapter prefix, for example:

```text
transport-example-rendered
transport-example-config-valid
transport-example-healthy
transport-example-reachable
```

Debug mode must expose the same phase and adapter diagnostics without changing
the deployment result or Vault commit rules.

## 8. Add tests before VPS deployment

At minimum add registry validation, Ansible syntax and lint, template
rendering, all six lifecycle operations, invalid input and missing-secret,
rollback state-retention, and removal-order tests.

The real VPS test is the final stage, not the first test. A new adapter must
pass Python tests, Bash tests, Ansible syntax, `ansible-lint`, `shellcheck`,
and template rendering before it is selectable in the menu.
