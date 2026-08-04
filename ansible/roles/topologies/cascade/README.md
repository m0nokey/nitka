# Cascade topology

Two VPS nodes form one logical deployment:

```text
client -> access transport -> ingress -> backhaul transport -> egress
```

Ingress owns access and routing. Egress owns the remote exit, Unbound, and
RPZ protection. The backhaul can be replaced independently when a future
adapter implements the shared contract.
