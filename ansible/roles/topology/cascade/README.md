# Cascade topology

Two VPS nodes form one logical deployment:

```text
client → access transport → ingress → backhaul transport → egress
```

Ingress owns access and routing. Egress owns the remote exit, Unbound, and
RPZ protection. The backhaul can be replaced independently when a future
adapter implements the shared contract.

## Traffic flow

```text
                             CASCADE TRAFFIC FLOW

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
                        ┌────────────────┐  ┌──────────────────┐
                        │ Local Internet │  │ SSH TUN          │
                        │ IP: ingress    │  │ ingress → egress │
                        └────────────────┘  └───────┬──────────┘
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
Client DIRECT  → user's ISP and public IP
Ingress DIRECT → ingress public IP
Egress PROXY   → egress public IP
```

Blocking happens at separate layers:

```text
Client REJECT → blocked locally by the client profile
Ingress BLOCK → dropped by Xray before forwarding
Egress RPZ    → blocked DNS name returns NXDOMAIN from Unbound
```
