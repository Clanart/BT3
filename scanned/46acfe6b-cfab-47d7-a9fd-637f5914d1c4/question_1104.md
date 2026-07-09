# Q1104: NEAR omni-types EVM event parser final settlement and later fee claim can diverge through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof_callback`` with control over proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields and desynchronize `near/omni-types/src/evm/events.rs::parse_evm_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `final settlement and later fee claim can diverge` attack class because maps a verified EVM log entry into a typed bridge `ProverResult` used by settlement, fee claim, and token deployment, violating `event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics`?

## Target
- File/function: `near/omni-types/src/evm/events.rs::parse_evm_proof`
- Entrypoint: `public EVM proof path through `verify_proof_callback``
- Attacker controls: proof kind, chain kind, raw log-entry bytes, topic layout, and ABI-decoded event fields
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: event parsing must not let one verified log be reinterpreted as a different bridge action with different recipient, fee, or token semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Also assert cross-module consistency between `near/omni-types/src/evm/events.rs::parse_evm_proof` and the adjacent proof parsing and source authentication after every branch.
