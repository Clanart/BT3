# Q4076: `parse_nonce_gen_first_response` and information returned in errors

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port use the distinct error paths of `parse_nonce_gen_first_response` in `core/src/rpc/parser/verifier.rs` to learn protocol state that enables a fund-moving action - which withdrawal indices are unserved, which connectors are unused, which deposits are half-signed - and combine it with a Bitcoin transaction to spend or strand a bridge UTXO?

## Target
- File/function: `core/src/rpc/parser/verifier.rs` -> `parse_nonce_gen_first_response`
- Entrypoint: repeated gRPC requests to the open port -> `parse_nonce_gen_first_response`
- Attacker controls: request parameters chosen to differentiate error paths; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: map bridge state, then act on a UTXO before the protocol does
- Invariant to test: error responses do not reveal state that changes what an attacker can do on chain
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: diff error responses across states and assert they are indistinguishable to an unauthenticated caller
