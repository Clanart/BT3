# Q1915: NEAR omni-types EVM event parser proof kind or event class confusion at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof_callback`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/events.rs::parse_evm_proof` violate `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics` in the `proof kind or event class confusion` attack class because maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
