# Q512: Misbind trusted context inside internal_handle_kickoff

## Question
Can an unprivileged attacker reach `internal_handle_kickoff` through auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff` and make attacker-controlled the streamed nonce-session identifiers and public nonce ordering bind to the wrong trusted context, so the deposit-to-move-tx binding is interpreted for one bridge action while authorizing another, violating the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/verifier.rs::internal_handle_kickoff
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff`
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: bind attacker-controlled the streamed nonce-session identifiers and public nonce ordering to the wrong trusted bridge context
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
