# Q2895: Decouple emergency protection in queue_relevant_txs_for_new_kickoff

## Question
Can an unprivileged attacker push attacker-controlled the `old_move_txid` in `ReplacementDeposit` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `queue_relevant_txs_for_new_kickoff` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the emergency-stop transaction that should protect the same deposit and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::queue_relevant_txs_for_new_kickoff
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
