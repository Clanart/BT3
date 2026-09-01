# Q3344: `nonce_distributor` and the optional ECDSA verification signature

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port bypass or replay the aggregator verification signature checked around `nonce_distributor` in `core/src/rpc/aggregator.rs` - because the check is skipped when `aggregator_verification_address` is unset, because the recovered message does not cover every field that matters, or because a signature for one request is valid for another?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `nonce_distributor`
- Entrypoint: a gRPC request to the open port -> `nonce_distributor`
- Attacker controls: all request fields, including those outside the signed message; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: act with a replayed or scope-mismatched authorisation signature
- Invariant to test: the fields covered by the verification signature == the fields that determine the action taken
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: mutate each unsigned field and assert `nonce_distributor` refuses the request
