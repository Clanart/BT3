# Q2385: Accept wrong transaction/proof shape in send_move_to_vault_tx

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes with malformed the transaction witness, anchor output, and script-path structure so `send_move_to_vault_tx` passes shallow structural checks while changing the transaction or proof semantics that later settlement relies on, corrupting the deposit context that the move transaction is treated as proving and violating the invariant that partial finalization state must not let an attacker inject a different vault spend path, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::send_move_to_vault_tx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the transaction witness, anchor output, and script-path structure
- Exploit idea: pass shallow structural checks while changing later settlement semantics using the transaction witness, anchor output, and script-path structure
- Invariant to test: partial finalization state must not let an attacker inject a different vault spend path
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
