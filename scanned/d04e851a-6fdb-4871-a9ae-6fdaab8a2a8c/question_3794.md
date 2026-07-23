# Q3794: Exploit anchor/output assumptions in aggregator_deposit_movetx_lands_onchain

## Question
Can an unprivileged attacker shape the transaction witness, anchor output, and script-path structure so `aggregator_deposit_movetx_lands_onchain` accepts an anchor, output, or script-path layout that no longer protects the intended bridge spend, corrupting the deposit context that the move transaction is treated as proving and violating the invariant that move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_movetx_lands_onchain
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the transaction witness, anchor output, and script-path structure
- Exploit idea: alter the anchor, output, or script-path layout using the transaction witness, anchor output, and script-path structure
- Invariant to test: move-to-vault acceptance must preserve the slashable/recoverable path for the same deposit
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
