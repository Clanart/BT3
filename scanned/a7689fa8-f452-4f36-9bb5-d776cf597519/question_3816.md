# Q3816: Replay context into internal_handle_kickoff

## Question
Can an unprivileged attacker use auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff` with attacker-controlled the `old_move_txid` in `ReplacementDeposit` so `internal_handle_kickoff` reuses a previously accepted context, causing the nofn aggregate key and covenant context to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/verifier.rs::internal_handle_kickoff
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff`
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: reuse or replay previously consumed the `old_move_txid` in `ReplacementDeposit` in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
