# Q937: Misbind trusted context inside on_round_tx_exit

## Question
Can an unprivileged attacker reach `on_round_tx_exit` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the deposit transaction timing, block placement, and confirmation ordering bind to the wrong trusted context, so the reimbursement path that must remain slashable and recoverable is interpreted for one bridge action while authorizing another, violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/states/round.rs::on_round_tx_exit
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: bind attacker-controlled the deposit transaction timing, block placement, and confirmation ordering to the wrong trusted bridge context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
