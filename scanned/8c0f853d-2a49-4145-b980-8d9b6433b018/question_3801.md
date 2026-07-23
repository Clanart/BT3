# Q3801: Exploit anchor/output assumptions in send_move_to_vault_tx

## Question
Can an unprivileged attacker shape the timing of the call relative to an in-flight or partially failed deposit finalization so `send_move_to_vault_tx` accepts an anchor, output, or script-path layout that no longer protects the intended bridge spend, corrupting the deposit context that the move transaction is treated as proving and violating the invariant that partial finalization state must not let an attacker inject a different vault spend path, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::send_move_to_vault_tx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the timing of the call relative to an in-flight or partially failed deposit finalization
- Exploit idea: alter the anchor, output, or script-path layout using the timing of the call relative to an in-flight or partially failed deposit finalization
- Invariant to test: partial finalization state must not let an attacker inject a different vault spend path
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
