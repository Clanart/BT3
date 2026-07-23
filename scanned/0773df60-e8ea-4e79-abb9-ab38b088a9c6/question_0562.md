# Q562: Misbind trusted context inside verify_signature

## Question
Can an unprivileged attacker reach `verify_signature` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the `deposit_outpoint` and its on-chain prevout details bind to the wrong trusted context, so the reimbursement path that must remain slashable and recoverable is interpreted for one bridge action while authorizing another, violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/actor.rs::verify_signature
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: bind attacker-controlled the `deposit_outpoint` and its on-chain prevout details to the wrong trusted bridge context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
