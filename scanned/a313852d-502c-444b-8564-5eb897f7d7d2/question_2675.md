# Q2675: NEAR omni-types EVM event parser partial EVM validation leaves exploitable gap

## Question
Can an unprivileged attacker provide an EVM proof to `public EVM proof path through `verify_proof_callback`` that passes inclusion checks in `near/omni-types/src/evm/events.rs::parse_evm_proof` while the decoded receipt or log still authorizes a different bridge action because of maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment, violating `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics`?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds.
