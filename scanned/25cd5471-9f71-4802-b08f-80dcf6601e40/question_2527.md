# Q2527: NEAR omni-types EVM event parser parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof_callback`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/events.rs::parse_evm_proof` violate `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics` in the `parser boundary or offset manipulation` attack class because maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
