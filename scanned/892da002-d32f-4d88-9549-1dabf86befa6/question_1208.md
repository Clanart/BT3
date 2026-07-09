# Q1208: NEAR required_balance_for_bind_token storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public `bind_token`` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` violate `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit` in the `storage withdrawal escapes live liabilities` attack class because quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
