# Q1209: NEAR required_balance_for_deploy_token storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public `deploy_token`` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` violate `deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable` in the `storage withdrawal escapes live liabilities` attack class because quotes storage for token deployment plus the downstream bind/storage needs that deployment implies becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token`
- Entrypoint: `internal accounting helper reached from public `deploy_token``
- Attacker controls: metadata size, token deployment path, and follow-up binding requirements
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
