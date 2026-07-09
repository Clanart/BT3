# Q1043: NEAR required_balance_for_deploy_token storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public `deploy_token`` with control over metadata size, token deployment path, and follow-up binding requirements and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because quotes storage for token deployment plus the downstream bind/storage needs that deployment implies, violating `deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token`
- Entrypoint: `internal accounting helper reached from public `deploy_token``
- Attacker controls: metadata size, token deployment path, and follow-up binding requirements
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` and the adjacent storage billing and refund bookkeeping after every branch.
