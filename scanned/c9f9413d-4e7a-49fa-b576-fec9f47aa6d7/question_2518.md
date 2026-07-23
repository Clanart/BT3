# Q2518: Cross-wire presigning material in is_utxo_spent

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `is_utxo_spent` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `evm_address` in `BaseDeposit`, so the deposit-to-move-tx binding is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/builder/block_cache.rs::is_utxo_spent
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
