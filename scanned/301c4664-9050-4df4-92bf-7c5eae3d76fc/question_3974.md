# Q3974: NEAR fin_transfer entry replay state keyed too narrowly for the true domain through cross-module drift

## Question
Can an unprivileged attacker use `public `fin_transfer` proof-submission flow` with control over proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay state keyed too narrowly for the true domain` attack class because verifies a proof through the configured prover, optionally prepays storage for recipient and fee accounts, then dispatches to `fin_transfer_callback`, violating `one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer`
- Entrypoint: `public `fin_transfer` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer` and the adjacent replay-protection bookkeeping after every branch.
