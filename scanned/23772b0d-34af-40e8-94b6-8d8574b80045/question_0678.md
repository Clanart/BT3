# Q678: NEAR fin_transfer entry recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public `fin_transfer` proof-submission flow` that makes `near/omni-bridge/src/lib.rs::fin_transfer` settle principal to one party but authorize fee claim or callback routing for another due to verifies a proof through the configured prover, optionally prepays storage for recipient and fee accounts, then dispatches to `fin_transfer_callback`, violating `one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer`
- Entrypoint: `public `fin_transfer` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
