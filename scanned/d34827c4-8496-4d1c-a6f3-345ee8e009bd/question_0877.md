# Q877: NEAR required_balance_for_deploy_token storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal accounting helper reached from public `deploy_token`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under quotes storage for token deployment plus the downstream bind/storage needs that deployment implies, violating `deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_deploy_token`
- Entrypoint: `internal accounting helper reached from public `deploy_token``
- Attacker controls: metadata size, token deployment path, and follow-up binding requirements
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: deploy quoting must never undercharge for the full state change needed to make a new wrapped token usable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
