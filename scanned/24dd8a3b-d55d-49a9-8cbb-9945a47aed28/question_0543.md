# Q543: NEAR required_balance_for_bind_token storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public `bind_token`` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` violate `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit` in the `storage quote underestimates live state` attack class because quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
