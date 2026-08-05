# Nitka Architecture Contract

Nitka has four independent layers. A layer may consume a contract from the
layer below it, but it must not contain the implementation of another layer.

```text
controller/UI -> deployment pipeline -> Ansible playbook
                                      -> system_base
                                      -> topology
                                      -> access transport
                                      -> backhaul transport
```

## Layers

### `system_base`

`system_base` is identical for every installation. It owns packages and
Docker, the deploy account, original SSH configuration and host-key backups,
temporary and final management SSH transitions, updater units, and system
cleanup after application removal.

It must not contain Xray, SSH proxy, SSH TUN, Cascade, or transport-specific
ports and credentials.

### `topology`

Topology describes where endpoints run and how nodes are related. It owns
composition, node roles, shared directories, and routing integration. It does
not define transport selection or transport credentials. Cascade ingress has a
deliberate composition exception: its Xray endpoint, Clash routing, and
backhaul network share one Compose project, so the topology role owns that
shared runtime wiring while the Xray state remains under the access contract.
Standalone access and every backhaul adapter are independently dispatched.

Current topology roles:

```text
ansible/roles/topology/cascade/ingress
ansible/roles/topology/cascade/egress
```

### `access`

Access is the client-to-ingress protocol. Examples:

```text
ansible/roles/transports/access/xray_reality
ansible/roles/transports/access/ssh_proxy
```

### `backhaul`

Backhaul is the ingress-to-egress protocol. The current adapter is:

```text
ansible/roles/transports/backhaul/ssh_tun
```

Access and backhaul are selected independently. A standalone deployment has an
access endpoint and no backhaul. A Cascade deployment has one access endpoint
on ingress and a paired backhaul client/server endpoint.

## VPS filesystem layout

Deployment IDs exist only in the encrypted Vault and controller state. They are
not used as remote directory names. Every VPS uses the same runtime layout:

```text
/opt/nitka/
├── standalone/access/xray-reality/
├── standalone/access/ssh-proxy/
└── cascade/
    ├── ingress/
    │   ├── compose.yml
    │   ├── access/xray-reality/
    │   ├── backhaul/ssh-tun/
    │   └── routing/clashrs/
    └── egress/
        ├── compose.yml
        ├── backhaul/ssh-tun/
        └── dns/
```

Management SSH remains separate in `/etc/ssh` and `/var/lib/nitka`; it is not
an access transport directory.

## Variable naming

Every Ansible variable has one architectural prefix:

```text
system_base_<concept>
topology_cascade_<node_role>_<concept>
access_<transport_id>_<concept>
backhaul_<transport_id>_<concept>
```

Examples:

```text
system_base_management_sshd_port
topology_cascade_ingress_remote_dir
access_xray_server_name
access_ssh_proxy_external_port
backhaul_ssh_tun_ssh_port
```

Selection and operation variables are pipeline inputs, not transport state:

```text
access_transport_selection
access_transport_operation
backhaul_transport_selection
backhaul_transport_operation
```

The following names are forbidden in new Ansible code:

```text
ssh_port
xray_state
transport_port
transports_ssh_*
cascade_ssh_tun_*
```

Management SSH and an access transport are different things. A management
port is always named `system_base_management_*`; a proxy port belongs to its
adapter namespace.

## Adapter lifecycle

Each implemented adapter exposes the same six operations:

```text
deploy
verify
restart
rollback_install
rollback_update
remove
```

The operation is dispatched by `roles/transports/access` or
`roles/transports/backhaul`. The adapter owns its files, containers,
credentials, health checks, and application cleanup. `system_base` owns only
the common VPS cleanup after adapter cleanup succeeds.

The meanings are deliberately different:

- `deploy`: install or render the desired version;
- `verify`: prove that the desired endpoint is healthy and reachable;
- `rollback_install`: remove a partially installed endpoint before the Vault
  transaction is committed;
- `rollback_update`: restore the previous working endpoint configuration;
- `remove`: remove the endpoint as part of returning the VPS to a clean state.

An installation transaction is committed only after `verify`, final
management SSH verification, and Vault persistence succeed. The pending Vault
transaction is retained on every failure.

## State ownership

The encrypted Vault is the source of truth. A pending operation contains the
candidate node state, selected topology/transports, credentials required to
resume, and the last completed phase. The main node list is not modified until
commit.

Vault schema version 2 stores node data only in `management`, `bootstrap`,
`access`, and `topology` blocks. `scripts/migrate_vault.py` is the only code
that understands the previous flat format. It runs once after Vault unlock,
validates the converted document, creates an encrypted backup before saving,
and writes an atomic v2 replacement. Runtime commands accept v2 only and fail
closed if an unmigrated document is supplied. No private key is written to
logs.

Transport credentials are namespaced by transport. Management credentials are
never reused as access or backhaul credentials.

## Rollback ownership

There are three rollback paths:

1. Installation rollback: common SSH transition restoration plus the selected
   adapter's `rollback_install` operation.
2. Working-service rollback: the selected adapter's `rollback_update`, using
   the previous Vault snapshot.
3. Application removal: the selected topology and adapters run `remove`, then
   `system_base` restores original SSH, packages, APT state, users, and host
   keys.

Cascade removal is ordered by node role. The endpoint and backhaul are removed
with the Cascade deployment variables before either node is cleaned by
`system_base`.
