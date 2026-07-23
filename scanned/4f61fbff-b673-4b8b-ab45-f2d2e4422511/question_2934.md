# Q2934: Decouple emergency protection in sign_taproot_pubkey_spend

## Question
Can an unprivileged attacker push attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests through public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `sign_taproot_pubkey_spend` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the nofn aggregate key and covenant context and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/actor.rs::sign_taproot_pubkey_spend
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
