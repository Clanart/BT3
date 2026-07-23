# Q1823: Leave reusable partial state in signaturesdb_encode_decode_invariant

## Question
Can an unprivileged attacker force a partial failure around the streamed nonce-session identifiers and public nonce ordering and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `signaturesdb_encode_decode_invariant` continues from stale intermediate state, causing the operator signature set attached to a deposit to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/wrapper.rs::signaturesdb_encode_decode_invariant
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: force a partial failure around the streamed nonce-session identifiers and public nonce ordering and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
