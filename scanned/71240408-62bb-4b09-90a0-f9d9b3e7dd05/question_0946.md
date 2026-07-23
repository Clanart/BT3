# Q946: Reuse stale partially-completed state in aggregator_two_deposit_movetx_and_emergency_stop

## Question
Can an unprivileged attacker replay or delay the claimed `deposit_outpoint` bound to that raw move transaction so `aggregator_two_deposit_movetx_and_emergency_stop` resumes from stale partially-completed state after the canonical bridge context changed, corrupting the deposit context that the move transaction is treated as proving and breaking the invariant that move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_two_deposit_movetx_and_emergency_stop
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the claimed `deposit_outpoint` bound to that raw move transaction
- Exploit idea: resume from stale partially completed state after canonical state changed via the claimed `deposit_outpoint` bound to that raw move transaction
- Invariant to test: move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
