# Q2478: Cross-wire presigning material in new_for_deposit

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `new_for_deposit` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `evm_address` in `BaseDeposit`, so the verifier nonce session that a final signature is supposed to consume is authorized under the wrong context and the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle breaks, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/builder/transaction/creator.rs::new_for_deposit
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
