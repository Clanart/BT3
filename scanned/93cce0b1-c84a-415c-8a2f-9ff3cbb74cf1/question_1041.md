# Q1041: NEAR required_balance_for_fast_transfer storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public fast-transfer paths` with control over fast-transfer id structure, relayer fields, and destination branch and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because computes storage reserved for relayer-sponsored fast transfer state, violating `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` and the adjacent storage billing and refund bookkeeping after every branch.
