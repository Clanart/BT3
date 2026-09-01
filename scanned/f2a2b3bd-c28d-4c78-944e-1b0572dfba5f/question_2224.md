# Q2224: `is_p2a_anchor` and retry semantics

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port create the failure condition that makes `is_p2a_anchor` in `crates/clementine-utils/src/address.rs` retry a fund-moving operation whose effect is not idempotent, so the retry produces a second on-chain action or a second signature for one protocol event?

## Target
- File/function: `crates/clementine-utils/src/address.rs` -> `is_p2a_anchor`
- Entrypoint: attacker-induced transient failures -> `is_p2a_anchor`
- Attacker controls: the condition that makes the first attempt appear to fail; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: turn a retry into a duplicated protocol action
- Invariant to test: retrying `is_p2a_anchor` produces at most one on-chain effect per protocol event
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: force retries and assert exactly-once effects
