# Deployment topologies

Topology selects the node relationship and composes access and backhaul
adapters. It does not contain transport-specific implementation details.

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
