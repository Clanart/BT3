# Q3728: NEAR utxo_fin_transfer_to_near callback unregister can sever state that callbacks still need

## Question
Can an unprivileged attacker combine `callback after storage checks for UTXO-to-Near settlement` with later callbacks so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` unregisters storage ownership before asynchronous cleanup runs, violating `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely.
