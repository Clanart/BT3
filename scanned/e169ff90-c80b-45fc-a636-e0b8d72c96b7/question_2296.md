# Q2296: `check_env_var` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `check_env_var` in `core/src/config/mod.rs` from a fund-moving path with input violating an invariant `check_env_var` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/config/mod.rs` -> `check_env_var` (This module defines configuration options)
- Entrypoint: an aggregator request or on-chain value -> `check_env_var`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `check_env_var` on a fund-moving path validates the invariant `check_env_var` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `check_env_var` with invariant-violating input and assert it fails closed
