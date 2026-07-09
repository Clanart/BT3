# Q710: NEAR required_balance_for_bind_token storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `internal accounting helper reached from public `bind_token`` and make `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` release storage funds that still back unresolved bridge state because of quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking, violating `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
