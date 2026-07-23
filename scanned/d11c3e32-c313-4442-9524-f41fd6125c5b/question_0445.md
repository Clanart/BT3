# Q445: Replay context into txiddb_encode_decode_invariant

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the `old_move_txid` in `ReplacementDeposit` so `txiddb_encode_decode_invariant` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/wrapper.rs::txiddb_encode_decode_invariant
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: reuse or replay previously consumed the `old_move_txid` in `ReplacementDeposit` in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
