# Q2894: NEAR utxo_fin_transfer_to_near callback storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `callback after storage checks for UTXO-to-Near settlement` with control over storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because sends tokens to the Near recipient after a UTXO-origin transfer and removes tracked finalization state on refund-like callback outcomes, violating `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` and the adjacent storage billing and refund bookkeeping after every branch.
