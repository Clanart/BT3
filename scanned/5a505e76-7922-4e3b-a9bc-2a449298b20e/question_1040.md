# Q1040: NEAR required_balance_for_fin_transfer storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public finalize paths` with control over destination branch and fee/storage action structure and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because computes how much storage balance a finalized transfer consumes on Near, violating `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` and the adjacent storage billing and refund bookkeeping after every branch.
