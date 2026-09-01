# Q2747: `parse_next_deposit_finalize_param_schnorr_sig` and the optional ECDSA verification signature

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port bypass or replay the aggregator verification signature checked around `parse_next_deposit_finalize_param_schnorr_sig` in `core/src/rpc/parser/verifier.rs` - because the check is skipped when `aggregator_verification_address` is unset, because the recovered message does not cover every field that matters, or because a signature for one request is valid for another?

## Target
- File/function: `core/src/rpc/parser/verifier.rs` -> `parse_next_deposit_finalize_param_schnorr_sig`
- Entrypoint: a gRPC request to the open port -> `parse_next_deposit_finalize_param_schnorr_sig`
- Attacker controls: all request fields, including those outside the signed message; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: act with a replayed or scope-mismatched authorisation signature
- Invariant to test: the fields covered by the verification signature == the fields that determine the action taken
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: mutate each unsigned field and assert `parse_next_deposit_finalize_param_schnorr_sig` refuses the request
