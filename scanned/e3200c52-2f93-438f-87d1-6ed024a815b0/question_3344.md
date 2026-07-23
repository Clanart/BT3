# Q3344: Break reimbursement recoverability in internal_handle_kickoff

## Question
Can an unprivileged attacker use auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff` with crafted the `recovery_taproot_address` in `BaseDeposit` so `internal_handle_kickoff` moves the protocol past the point where reimbursement should remain recoverable, leaving the deposit-to-move-tx binding inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/verifier.rs::internal_handle_kickoff
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff`
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
