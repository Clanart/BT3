# Q2088: `flatten_join_named_results` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `flatten_join_named_results` in `core/src/utils.rs` from a fund-moving path with input violating an invariant `flatten_join_named_results` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/utils.rs` -> `flatten_join_named_results`
- Entrypoint: an aggregator request or on-chain value -> `flatten_join_named_results`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `flatten_join_named_results` on a fund-moving path validates the invariant `flatten_join_named_results` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `flatten_join_named_results` with invariant-violating input and assert it fails closed
