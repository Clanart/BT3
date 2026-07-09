# Q3722: NEAR fin_transfer entry replay state keyed too narrowly for the true domain

## Question
Can an unprivileged attacker exploit `public `fin_transfer` proof-submission flow` so that `near/omni-bridge/src/lib.rs::fin_transfer` treats two events from different chains, assets, or message classes as sharing one replay slot because of verifies a proof through the configured prover, optionally prepays storage for recipient and fee accounts, then dispatches to `fin_transfer_callback`, violating `one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer`
- Entrypoint: `public `fin_transfer` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields.
- Invariant to test: one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly.
