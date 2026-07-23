# Q895: Misbind trusted context inside ready_to_reimburse

## Question
Can an unprivileged attacker reach `ready_to_reimburse` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the `old_move_txid` in `ReplacementDeposit` bind to the wrong trusted context, so the nofn aggregate key and covenant context is interpreted for one bridge action while authorizing another, violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/states/round.rs::ready_to_reimburse
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: bind attacker-controlled the `old_move_txid` in `ReplacementDeposit` to the wrong trusted bridge context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
