# Q1441: Exploit anchor/output assumptions in send_move_to_vault_tx

## Question
Can an unprivileged attacker shape the claimed `deposit_outpoint` bound to that raw move transaction so `send_move_to_vault_tx` accepts an anchor, output, or script-path layout that no longer protects the intended bridge spend, corrupting the move-to-vault transaction accepted for a deposit and violating the invariant that move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::send_move_to_vault_tx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the claimed `deposit_outpoint` bound to that raw move transaction
- Exploit idea: alter the anchor, output, or script-path layout using the claimed `deposit_outpoint` bound to that raw move transaction
- Invariant to test: move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
