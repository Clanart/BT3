# Q3269: Decouple emergency protection in save_block_infos_within_range

## Question
Can an unprivileged attacker push attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `save_block_infos_within_range` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the reimbursement path that must remain slashable and recoverable and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/header_chain_prover.rs::save_block_infos_within_range
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
