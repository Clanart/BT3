# Q2248: `get_compatibility_params` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `get_compatibility_params` in `core/src/compatibility.rs` from a fund-moving path with input violating an invariant `get_compatibility_params` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/compatibility.rs` -> `get_compatibility_params` (This module contains the logic for checking compatibility between actors in the system)
- Entrypoint: an aggregator request or on-chain value -> `get_compatibility_params`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `get_compatibility_params` on a fund-moving path validates the invariant `get_compatibility_params` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `get_compatibility_params` with invariant-violating input and assert it fails closed
