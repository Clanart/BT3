# Q40: NEAR required_balance_for_deploy_token storage quote underestimates live state

## Question
Can an unprivileged attacker reach `internal accounting helper reached from public `deploy_token`` and make `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` reserve less storage than the live bridge state actually consumes because of quotes storage for token deployment plus the downstream bind/storage needs that deployment implies, violating `deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token`
- Entrypoint: `internal accounting helper reached from public `deploy_token``
- Attacker controls: metadata size, token deployment path, and follow-up binding requirements
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
