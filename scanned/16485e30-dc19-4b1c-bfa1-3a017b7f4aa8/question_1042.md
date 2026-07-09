# Q1042: NEAR required_balance_for_bind_token storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public `bind_token`` with control over foreign token address, decimals, and lock-row creation and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because quotes storage needed to bind an existing Near token to a foreign asset and add lock tracking, violating `binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_bind_token`
- Entrypoint: `internal accounting helper reached from public `bind_token``
- Attacker controls: foreign token address, decimals, and lock-row creation
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: binding quote logic must not let attackers create mappings or lock rows that outgrow the prepaid deposit
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_bind_token` and the adjacent storage billing and refund bookkeeping after every branch.
