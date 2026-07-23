# Q25: Accept wrong transaction/proof shape in send_move_to_vault_tx

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes with malformed the timing of the call relative to an in-flight or partially failed deposit finalization so `send_move_to_vault_tx` passes shallow structural checks while changing the transaction or proof semantics that later settlement relies on, corrupting the move-to-vault transaction accepted for a deposit and violating the invariant that move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::send_move_to_vault_tx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the timing of the call relative to an in-flight or partially failed deposit finalization
- Exploit idea: pass shallow structural checks while changing later settlement semantics using the timing of the call relative to an in-flight or partially failed deposit finalization
- Invariant to test: move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
