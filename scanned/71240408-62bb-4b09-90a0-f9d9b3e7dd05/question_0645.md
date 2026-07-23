# Q645: Misbind trusted context inside compute_hash_of_round_txs

## Question
Can an unprivileged attacker reach `compute_hash_of_round_txs` through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline and make attacker-controlled the streamed nonce-session identifiers and public nonce ordering bind to the wrong trusted context, so the verifier nonce session that a final signature is supposed to consume is interpreted for one bridge action while authorizing another, violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/sighash.rs::compute_hash_of_round_txs
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: bind attacker-controlled the streamed nonce-session identifiers and public nonce ordering to the wrong trusted bridge context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
