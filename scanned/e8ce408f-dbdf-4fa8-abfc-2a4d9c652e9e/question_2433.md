# Q2433: Cross-wire presigning material in get_signed_txs_for_kickoff

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `get_signed_txs_for_kickoff` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `evm_address` in `BaseDeposit`, so the nofn aggregate key and covenant context is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/verifier.rs::get_signed_txs_for_kickoff
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
