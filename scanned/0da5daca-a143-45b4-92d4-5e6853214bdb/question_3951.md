# Q3951: Replay context into create_disprove_txhandler

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the `deposit_outpoint` and its on-chain prevout details so `create_disprove_txhandler` reuses a previously accepted context, causing the operator signature set attached to a deposit to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/transaction/challenge.rs::create_disprove_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: reuse or replay previously consumed the `deposit_outpoint` and its on-chain prevout details in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
