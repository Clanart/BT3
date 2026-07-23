# Q2856: Decouple emergency protection in internal_send_tx

## Question
Can an unprivileged attacker push attacker-controlled the `old_move_txid` in `ReplacementDeposit` through public gRPC `ClementineAggregator.InternalSendTx` request so `internal_send_tx` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the emergency-stop transaction that should protect the same deposit and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_send_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalSendTx` request
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
