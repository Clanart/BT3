# Q3766: Break reimbursement recoverability in initial_collateral

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the `deposit_outpoint` and its on-chain prevout details so `initial_collateral` moves the protocol past the point where reimbursement should remain recoverable, leaving the nofn aggregate key and covenant context inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/states/round.rs::initial_collateral
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
