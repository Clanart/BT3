# Q37: Replay context into internal_create_watchtower_challenge

## Question
Can an unprivileged attacker use auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge` with attacker-controlled the `deposit_outpoint` and its on-chain prevout details so `internal_create_watchtower_challenge` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/verifier.rs::internal_create_watchtower_challenge
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge`
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: reuse or replay previously consumed the `deposit_outpoint` and its on-chain prevout details in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
