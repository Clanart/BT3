# Q1694: NEAR required_balance_for_fast_transfer storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public fast-transfer paths` with control over fast-transfer id structure, relayer fields, and destination branch and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because computes storage reserved for relayer-sponsored fast transfer state, violating `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` and the adjacent storage billing and refund bookkeeping after every branch.
