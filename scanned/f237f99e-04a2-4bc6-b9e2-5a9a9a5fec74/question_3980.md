# Q3980: NEAR utxo_fin_transfer_to_near callback unregister can sever state that callbacks still need through cross-module drift

## Question
Can an unprivileged attacker use `callback after storage checks for UTXO-to-Near settlement` with control over storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `unregister can sever state that callbacks still need` attack class because sends tokens to the Near recipient after a UTXO-origin transfer and removes tracked finalization state on refund-like callback outcomes, violating `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` and the adjacent storage billing and refund bookkeeping after every branch.
