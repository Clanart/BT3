# Q1182: NEAR utxo_fin_transfer_to_near callback delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `callback after storage checks for UTXO-to-Near settlement` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` violate `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id` in the `delivery callback leaves inconsistent state` attack class because sends tokens to the Near recipient after a UTXO-origin transfer and removes tracked finalization state on refund-like callback outcomes becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
