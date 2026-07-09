# Q1176: NEAR fin_transfer entry recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public `fin_transfer` proof-submission flow` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::fin_transfer` violate `one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized` in the `recipient or fee-recipient rebinding` attack class because verifies a proof through the configured prover, optionally prepays storage for recipient and fee accounts, then dispatches to `fin_transfer_callback` becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer`
- Entrypoint: `public `fin_transfer` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
