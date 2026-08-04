# Public configuration examples

The repository root contains two sanitized working examples:

- `shadowrocket.ru.example.conf` — client-side Shadowrocket profile for iOS/macOS;
- `ingress-routing.ru.example.yml` — server-side Xray routing policy.

## Routing policy

`ingress-routing.ru.example.yml` is imported into Nitka and rendered into the
ingress Xray configuration; it is not copied directly to a VPS or imported
into a client application.

For a personal copy, use the mounted import directory:

```text
$HOME/.local/state/nitka/imports/routing/
```

Inside the controller container the same directory is:

```text
/state/nitka/imports/routing/
```

## Traffic routing model

```text
                             TRAFFIC ROUTING


    ┌──────────────────────────────┐
    │ Client device                │
    │ Shadowrocket / client        │
    └───────────────┬──────────────┘
                    │
            ┌───────┴────────┐
            │                │
          DIRECT           PROXY
            │                │
            ▼                ▼
    ┌────────────────┐  ┌──────────────────────────────┐
    │ User ISP       │  │ Ingress node                 │
    │ local Internet │  │ Xray routing policy          │
    │ IP: user       │  └───────────────┬──────────────┘
    └────────────────┘                  │
                                 ┌──────┴──────┐
                                 │             │
                               DIRECT        PROXY
                                 │             │
                                 ▼             ▼
                        ┌────────────────┐  ┌────────────────┐
                        │ Local Internet │  │ SSH TUN        │
                        │ IP: ingress    │  │ ingress→egress │
                        └────────────────┘  └───────┬────────┘
                                                    │
                                                    ▼
                                          ┌────────────────────────┐
                                          │ Egress node            │
                                          │ Unbound + RPZ          │
                                          └────────────┬───────────┘
                                                       │
                                                       ▼
                                          ┌────────────────────────┐
                                          │ Remote Internet        │
                                          │ IP: egress             │
                                          └────────────────────────┘
```

The three exits are different:

```text
Client DIRECT → user's ISP and public IP
Ingress DIRECT → ingress public IP
Egress PROXY → egress public IP
```

The Russian example is direct-by-default on the client. This keeps ordinary
traffic on the user's local exit if a selected proxy route or application
stops working.

Blocking happens at separate layers:

```text
Client REJECT → blocked locally by the client profile
Ingress BLOCK → dropped by Xray before DIRECT/PROXY forwarding
Egress RPZ    → blocked DNS name returns NXDOMAIN from Unbound
```

## Shadowrocket profile

`shadowrocket.ru.example.conf` is imported into Shadowrocket on the client device. It is
not uploaded to the ingress or egress server. Nitka replaces these runtime
placeholders when generating a real profile:

```text
{ingress_node}
{egress_node}
```

The direct `IP-CIDR` rules for both node addresses must remain above general
proxy rules so the VPN endpoints cannot be routed back through the VPN.

The profile is a Russian-region example with client-side ad/tracking rejects;
it is not the universal default for every deployment.

## Supported client

The public client example targets Shadowrocket on iOS and macOS. It is the
only client format currently documented and tested end to end.

Public examples must not contain real server IPs, UUIDs, private keys,
passwords, or management credentials.
