# Q2287: Confuse replacement linkage in load_kickoff_machines

## Question
Can an unprivileged attacker shape the `deposit_outpoint` and its on-chain prevout details so `load_kickoff_machines` confuses replacement and non-replacement contexts, causing the nofn aggregate key and covenant context to inherit the wrong history and violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/state_machine.rs::load_kickoff_machines
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
