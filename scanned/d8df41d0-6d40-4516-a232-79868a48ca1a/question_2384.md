# Q2384: `combine_errors` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `combine_errors` in `core/src/utils.rs` from a fund-moving path with input violating an invariant `combine_errors` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/utils.rs` -> `combine_errors`
- Entrypoint: an aggregator request or on-chain value -> `combine_errors`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `combine_errors` on a fund-moving path validates the invariant `combine_errors` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `combine_errors` with invariant-violating input and assert it fails closed
