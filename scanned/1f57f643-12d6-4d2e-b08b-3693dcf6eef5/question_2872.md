# Q2872: Decouple emergency protection in internal_handle_kickoff

## Question
Can an unprivileged attacker push attacker-controlled the `evm_address` in `BaseDeposit` through auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff` so `internal_handle_kickoff` advances the main settlement path while the emergency-stop or recovery path remains tied to a different context, corrupting the reimbursement path that must remain slashable and recoverable and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/verifier.rs::internal_handle_kickoff
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalHandleKickoff`
- Attacker controls: the `evm_address` in `BaseDeposit`
- Exploit idea: advance the main path while protection/recovery remains tied to another context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
