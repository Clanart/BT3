# Q1693: NEAR required_balance_for_fin_transfer storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public finalize paths` with control over destination branch and fee/storage action structure and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because computes how much storage balance a finalized transfer consumes on Near, violating `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` and the adjacent storage billing and refund bookkeeping after every branch.
