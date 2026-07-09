# Q876: NEAR required_balance_for_bind_token storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal accounting helper reached from public `bind_token`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking, violating `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
