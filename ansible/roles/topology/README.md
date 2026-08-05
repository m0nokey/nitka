# Deployment topologies

Topology selects the node relationship and composes access and backhaul
adapters. Cascade ingress has one shared Compose runtime for Xray, routing,
and backhaul networking; its topology role owns that composition wiring while
transport state and credentials remain namespaced under the selected adapter.

```yaml
topology: standalone
access:
  transport: xray-reality
```

```yaml
topology: cascade
access:
  transport: xray-reality
backhaul:
  transport: ssh-tun
```

The deployment state and existing playbooks remain the source of executable
behavior. These directories define the extension boundary for future
topology adapters.
