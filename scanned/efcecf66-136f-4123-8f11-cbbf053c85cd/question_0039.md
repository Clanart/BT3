# Q39: NEAR required_balance_for_bind_token storage quote underestimates live state

## Question
Can an unprivileged attacker reach `internal accounting helper reached from public `bind_token`` and make `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` reserve less storage than the live bridge state actually consumes because of quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking, violating `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
