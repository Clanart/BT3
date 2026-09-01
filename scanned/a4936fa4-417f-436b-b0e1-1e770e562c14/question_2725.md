# Q2725: `parse_deposit_finalize_param_emergency_stop_agg_nonce` and unvalidated protobuf field conversion

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port send a request whose fields survive `parse_deposit_finalize_param_emergency_stop_agg_nonce` in `core/src/rpc/parser/verifier.rs` while violating an invariant the rest of the protocol assumes - an address parsed for the wrong network via `assume_checked`, a non-canonical public key, an index past the end of a list, a Winternitz key beyond `the module's protocol constant`, or a `sighash_type` byte the protocol does not expect?

## Target
- File/function: `core/src/rpc/parser/verifier.rs` -> `parse_deposit_finalize_param_emergency_stop_agg_nonce`
- Entrypoint: a gRPC request to the open port -> `parse_deposit_finalize_param_emergency_stop_agg_nonce`
- Attacker controls: every byte of the protobuf message; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: smuggle an out-of-domain value into signing or transaction construction
- Invariant to test: every value `parse_deposit_finalize_param_emergency_stop_agg_nonce` emits satisfies the domain invariants its consumers assume
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: fuzz `parse_deposit_finalize_param_emergency_stop_agg_nonce` with adversarial protobuf and assert malformed input never reaches signing
