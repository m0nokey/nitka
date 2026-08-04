# Xray access transport

Status: implemented.

This adapter is the client-facing access layer for standalone and Cascade
deployments. Its current Ansible implementation is kept in the existing
`ansible/roles/xray` and `ansible/roles/cascade_ingress` roles; this directory
defines the stable transport boundary and is not a second Xray stack.

Responsibilities:

- expose VLESS access links with the selected Xray transport;
- render Xray inbounds and access-key state;
- pass traffic to the topology routing layer;
- keep access credentials separate from management and backhaul credentials.

The currently supported public client profile is Shadowrocket for iOS/macOS.
