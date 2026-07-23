# Q2362: Accept wrong transaction/proof shape in aggregator_two_deposit_movetx_and_emergency_stop

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes with malformed the raw move-to-vault transaction bytes so `aggregator_two_deposit_movetx_and_emergency_stop` passes shallow structural checks while changing the transaction or proof semantics that later settlement relies on, corrupting the deposit context that the move transaction is treated as proving and violating the invariant that move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_two_deposit_movetx_and_emergency_stop
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the raw move-to-vault transaction bytes
- Exploit idea: pass shallow structural checks while changing later settlement semantics using the raw move-to-vault transaction bytes
- Invariant to test: move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
