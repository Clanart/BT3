# Q2013: `transfer_to_btc_wallet` and the `Internal` method-name prefix gate

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `transfer_to_btc_wallet` in `core/src/rpc/operator.rs` despite its `Internal` designation - by exploiting that `is_internal` classifies methods from a `grpc-method` metadata header added by middleware rather than from the routing table, or that the gate is skipped entirely on the entity where `transfer_to_btc_wallet` lives?

## Target
- File/function: `core/src/rpc/operator.rs` -> `transfer_to_btc_wallet`
- Entrypoint: a crafted gRPC request with controlled metadata -> `transfer_to_btc_wallet`
- Attacker controls: request path, gRPC metadata headers, and message body; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: invoke an internal-only method from outside
- Invariant to test: a caller reaching an `Internal*` method == the entity itself
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: send a request with a forged/absent `grpc-method` header and assert `transfer_to_btc_wallet` is refused
