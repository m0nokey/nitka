# Public examples

This tree contains client-facing examples only. The currently supported and
tested client format is Shadowrocket for iOS and macOS.

```text
examples/
├── clients/
│   └── shadowrocket/
│       └── README.md
└── routing/
    └── xray/
        └── README.md
```

The sanitized files currently remain at the repository root for compatibility
with existing imports:

- `shadowrocket.ru.example.conf`
- `ingress-routing.ru.example.yml`

They are examples, not deployment state. Real node addresses, credentials,
UUIDs, and private keys are supplied from the local encrypted Vault at
rendering time.
