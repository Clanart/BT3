# Q3972: Replay context into calculate_shared_txins_sighash

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with attacker-controlled the `recovery_taproot_address` in `BaseDeposit` so `calculate_shared_txins_sighash` reuses a previously accepted context, causing the verifier nonce session that a final signature is supposed to consume to be consumed twice and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/builder/transaction/txhandler.rs::calculate_shared_txins_sighash
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: reuse or replay previously consumed the `recovery_taproot_address` in `BaseDeposit` in a fresh context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
