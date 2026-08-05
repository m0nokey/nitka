# Public configuration examples

This directory contains sanitized examples for the two-node Cascade topology.
They are not standalone-node client or routing profiles:

```text
examples/
└── cascade/
    ├── clients/shadowrocket/
    │   ├── README.md
    │   └── shadowrocket.ru.example.conf
    └── routing/xray/
        ├── README.md
        └── ingress-routing.ru.example.yml
```

The files contain placeholders only. They are not deployment state and must
not contain real server addresses, UUIDs, private keys, passwords, or
management credentials.

## Shadowrocket client

The Cascade client example is
[`cascade/clients/shadowrocket/shadowrocket.ru.example.conf`](cascade/clients/shadowrocket/shadowrocket.ru.example.conf)
for Shadowrocket on iOS and macOS. It demonstrates the three client actions:

```text
DIRECT  user's local exit
PROXY   ingress and, where selected by Xray, the egress exit
REJECT  local client-side blocking
```

It uses `{ingress_node}` and `{egress_node}` placeholders. Nitka replaces
them when generating a real client profile. The profile is a Russian-region
example with client-side ad and tracking rejects, not a universal default.

## Cascade routing

The Xray routing policy is
[`cascade/routing/xray/ingress-routing.ru.example.yml`](cascade/routing/xray/ingress-routing.ru.example.yml).
The traffic flow and blocking layers are documented with the Cascade topology
in [`ansible/roles/topology/cascade/README.md`](../ansible/roles/topology/cascade/README.md).

The policy is imported into Nitka and rendered into the ingress Xray
configuration. It is not copied directly to a VPS or imported into a client
application.

For a personal copy, use the mounted import directory:

```text
$HOME/.local/state/nitka/imports/routing/
```

Inside the controller container the same directory is:

```text
/state/nitka/imports/routing/
```
