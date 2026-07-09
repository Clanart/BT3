# Q3323: NEAR utxo_fin_transfer_to_near callback refund goes to wrong logical owner via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after storage checks for UTXO-to-Near settlement` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `refund goes to wrong logical owner` under sends tokens to the Near recipient after a UTXO-origin transfer and removes tracked finalization state on refund-like callback outcomes, violating `UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_near_callback`
- Entrypoint: `callback after storage checks for UTXO-to-Near settlement`
- Attacker controls: storage-check result, token id, recipient account, amount, UTXO transfer message, and origin chain
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO completion and rollback must stay consistent so callbacks cannot both pay a recipient and reopen the same origin transfer id
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
