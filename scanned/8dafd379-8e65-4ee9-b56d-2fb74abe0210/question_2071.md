# Q2071: NEAR omni-types EVM event parser parser boundary or offset manipulation

## Question
Can an unprivileged attacker craft proof bytes for `public EVM proof path through `verify_proof_callback`` that make `near/omni-types/src/evm/events.rs::parse_evm_proof` shift field boundaries, truncate payloads, or reinterpret trailing bytes because of maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment, violating `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics`?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields.
