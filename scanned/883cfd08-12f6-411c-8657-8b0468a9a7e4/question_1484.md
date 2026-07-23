# Q1484: Leave reusable partial state in send_next_round_tx

## Question
Can an unprivileged attacker force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `send_next_round_tx` continues from stale intermediate state, causing the operator signature set attached to a deposit to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/operator.rs::send_next_round_tx
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: force a partial failure around the deposit transaction timing, block placement, and confirmation ordering and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
