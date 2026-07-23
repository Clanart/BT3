# Q3406: Break reimbursement recoverability in sign_taproot_pubkey_spend

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the set of verifier, operator, or watchtower keys that get associated with the deposit context so `sign_taproot_pubkey_spend` moves the protocol past the point where reimbursement should remain recoverable, leaving the verifier nonce session that a final signature is supposed to consume inconsistent with the assumption that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, and leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/actor.rs::sign_taproot_pubkey_spend
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
