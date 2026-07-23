# Q3341: Break reimbursement recoverability in internal_create_watchtower_challenge

## Question
Can an unprivileged attacker use auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge` with crafted the set of verifier, operator, or watchtower keys that get associated with the deposit context so `internal_create_watchtower_challenge` moves the protocol past the point where reimbursement should remain recoverable, leaving the nofn aggregate key and covenant context inconsistent with the assumption that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/verifier.rs::internal_create_watchtower_challenge
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge`
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
