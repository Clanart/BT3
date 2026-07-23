# Q3756: Break reimbursement recoverability in match_block

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the deposit transaction timing, block placement, and confirmation ordering so `match_block` moves the protocol past the point where reimbursement should remain recoverable, leaving the deposit-to-move-tx binding inconsistent with the assumption that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/states/matcher.rs::match_block
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
