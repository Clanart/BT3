# Q3593: NEAR utxo_fin_transfer_to_near callback refund goes to wrong logical owner at boundary values

## Question
Can an unprivileged attacker trigger `callback after storage checks for UTXO-to-Near settlement` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` violate `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id` in the `refund goes to wrong logical owner` attack class because sends tokens to the Near recipient after a UTXO-origin transfer and removes tracked finalization state on refund-like callback outcomes becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
