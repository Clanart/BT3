# Q1420: Leave reusable partial state in aggregator_deposit_nonce_stream_creation_verifier_timeout

## Question
Can an unprivileged attacker force a partial failure around the `evm_address` in `BaseDeposit` and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `aggregator_deposit_nonce_stream_creation_verifier_timeout` continues from stale intermediate state, causing the deposit-to-move-tx binding to diverge from the canonical bridge context and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_nonce_stream_creation_verifier_timeout
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: force a partial failure around the `evm_address` in `BaseDeposit` and then resume under changed state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
