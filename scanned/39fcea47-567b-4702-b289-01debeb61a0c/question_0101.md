# Q101: NEAR omni-types EVM event parser recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public EVM proof path through `verify_proof_callback`` that makes `near/omni-types/src/evm/events.rs::parse_evm_proof` settle principal to one party but authorize fee claim or callback routing for another due to maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment, violating `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics`?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
