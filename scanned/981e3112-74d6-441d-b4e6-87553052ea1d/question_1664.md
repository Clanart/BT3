# Q1664: NEAR fin_transfer entry asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `public `fin_transfer` proof-submission flow` with control over proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because verifies a proof through the configured prover, optionally prepays storage for recipient and fee accounts, then dispatches to `fin_transfer_callback`, violating `one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer`
- Entrypoint: `public `fin_transfer` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, storage-deposit actions, attached deposit, and ordering of storage actions
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one valid inbound proof must settle exactly once, on the correct asset and branch, without letting storage preparation alter what economic event is finalized
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer` and the adjacent replay-protection bookkeeping after every branch.
