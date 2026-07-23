# Q984: Race internal_handle_kickoff across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff` interactions around the aggregate nonce / partial-signature sequencing across repeated requests so `internal_handle_kickoff` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, and leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/verifier.rs::internal_handle_kickoff
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff`
- Attacker controls: the aggregate nonce / partial-signature sequencing across repeated requests
- Exploit idea: use retries, batching, or timing around the aggregate nonce / partial-signature sequencing across repeated requests to desynchronize state
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
