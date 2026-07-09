# Q376: NEAR required_balance_for_deploy_token storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public `deploy_token`` with control over metadata size, token deployment path, and follow-up binding requirements and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because quotes storage for token deployment plus the downstream bind/storage needs that deployment implies, violating `deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token`
- Entrypoint: `internal accounting helper reached from public `deploy_token``
- Attacker controls: metadata size, token deployment path, and follow-up binding requirements
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` and the adjacent storage billing and refund bookkeeping after every branch.
