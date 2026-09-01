# Q1765: `send_move_to_vault_tx` and the optional ECDSA verification signature

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port bypass or replay the aggregator verification signature checked around `send_move_to_vault_tx` in `core/src/rpc/aggregator.rs` - because the check is skipped when `aggregator_verification_address` is unset, because the recovered message does not cover every field that matters, or because a signature for one request is valid for another?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `send_move_to_vault_tx`
- Entrypoint: a gRPC request to the open port -> `send_move_to_vault_tx`
- Attacker controls: all request fields, including those outside the signed message; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: act with a replayed or scope-mismatched authorisation signature
- Invariant to test: the fields covered by the verification signature == the fields that determine the action taken
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: mutate each unsigned field and assert `send_move_to_vault_tx` refuses the request
