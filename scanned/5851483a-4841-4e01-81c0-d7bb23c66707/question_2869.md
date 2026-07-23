# Q2869: Decouple emergency protection in internal_create_watchtower_challenge

## Question
Can an unprivileged attacker push attacker-controlled the aggregate nonce / partial-signature sequencing across repeated requests through auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge` so `internal_create_watchtower_challenge` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the deposit-to-move-tx binding and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/verifier.rs::internal_create_watchtower_challenge
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge`
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
