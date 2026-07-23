# Q1517: Leave reusable partial state in tx_sign_and_fill_sigs

## Question
Can an unprivileged attacker force a partial failure around the streamed nonce-session identifiers and public nonce ordering and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `tx_sign_and_fill_sigs` continues from stale intermediate state, causing the deposit-to-move-tx binding to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/actor.rs::tx_sign_and_fill_sigs
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: force a partial failure around the streamed nonce-session identifiers and public nonce ordering and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
