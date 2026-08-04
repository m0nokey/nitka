# Xray routing example

`ingress-routing.ru.example.yml` at the repository root is the public Xray
routing example. It is imported by Nitka and rendered into the ingress Xray
configuration; it is not copied directly to a VPS or imported into
Shadowrocket.

The routing policy is intentionally separate from the access transport. It
defines which traffic remains `DIRECT`, which traffic uses the ingress
backhaul endpoint and egress exit, and which traffic is blocked at ingress.
