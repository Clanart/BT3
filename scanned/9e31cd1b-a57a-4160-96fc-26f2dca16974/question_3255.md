# Q3255: NEAR omni-types EVM event parser optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `public EVM proof path through `verify_proof_callback`` so that `near/omni-types/src/evm/events.rs::parse_evm_proof` authenticates one payload but downstream logic interprets another because of maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment, violating `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics`?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
