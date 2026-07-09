# Q1270: NEAR omni-types EVM event parser final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof_callback`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/events.rs::parse_evm_proof` violate `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics` in the `final settlement and later fee claim can diverge` attack class because maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
