# Q1016: NEAR utxo_fin_transfer_to_near callback delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `callback after storage checks for UTXO-to-Near settlement` with control over storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because sends tokens to the Near recipient after a UTXO-origin transfer and removes tracked finalization state on refund-like callback outcomes, violating `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` and the adjacent storage billing and refund bookkeeping after every branch.
