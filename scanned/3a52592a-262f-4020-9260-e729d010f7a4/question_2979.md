# Q2979: Decouple emergency protection in create_key_spend_tx_handler

## Question
Can an unprivileged attacker push attacker-controlled the `old_move_txid` in `ReplacementDeposit` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_key_spend_tx_handler` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the operator signature set attached to a deposit and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/actor.rs::create_key_spend_tx_handler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
