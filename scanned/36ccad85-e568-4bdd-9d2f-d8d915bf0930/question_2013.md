# Q2013: Confuse replacement linkage in create_emergency_stop_txhandler

## Question
Can an unprivileged attacker shape the `old_move_txid` in `ReplacementDeposit` so `create_emergency_stop_txhandler` confuses replacement and non-replacement contexts, causing the reimbursement path that must remain slashable and recoverable to inherit the wrong history and violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/transaction/mod.rs::create_emergency_stop_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
