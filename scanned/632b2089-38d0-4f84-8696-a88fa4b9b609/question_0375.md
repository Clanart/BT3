# Q375: NEAR required_balance_for_bind_token storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public `bind_token`` with control over foreign token address, decimals, and lock-row creation and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking, violating `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` and the adjacent storage billing and refund bookkeeping after every branch.
