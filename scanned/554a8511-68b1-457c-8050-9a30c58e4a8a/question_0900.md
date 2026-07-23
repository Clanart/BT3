# Q900: Misbind trusted context inside update_block_as_canonical

## Question
Can an unprivileged attacker reach `update_block_as_canonical` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the deposit transaction timing, block placement, and confirmation ordering bind to the wrong trusted context, so the verifier nonce session that a final signature is supposed to consume is interpreted for one bridge action while authorizing another, violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/bitcoin_syncer.rs::update_block_as_canonical
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the deposit transaction timing, block placement, and confirmation ordering
- Exploit idea: bind attacker-controlled the deposit transaction timing, block placement, and confirmation ordering to the wrong trusted bridge context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
