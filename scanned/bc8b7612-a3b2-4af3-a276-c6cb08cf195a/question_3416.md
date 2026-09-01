# Q3416: `expected_msg_got_none` and the optional ECDSA verification signature

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port bypass or replay the aggregator verification signature checked around `expected_msg_got_none` in `core/src/rpc/error.rs` - because the check is skipped when `aggregator_verification_address` is unset, because the recovered message does not cover every field that matters, or because a signature for one request is valid for another?

## Target
- File/function: `core/src/rpc/error.rs` -> `expected_msg_got_none`
- Entrypoint: a gRPC request to the open port -> `expected_msg_got_none`
- Attacker controls: all request fields, including those outside the signed message; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: act with a replayed or scope-mismatched authorisation signature
- Invariant to test: the fields covered by the verification signature == the fields that determine the action taken
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: mutate each unsigned field and assert `expected_msg_got_none` refuses the request
