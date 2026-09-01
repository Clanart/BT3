# Q3836: `parse_deposit_sign_session` and disclosure of pre-reveal protocol material

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port call `parse_deposit_sign_session` in `core/src/rpc/parser/verifier.rs` to obtain protocol material before its intended reveal - a Winternitz preimage, a challenge-ack preimage, an unbroadcast presigned transaction, an emergency-stop transaction - and use it to spend or invalidate a bridge UTXO?

## Target
- File/function: `core/src/rpc/parser/verifier.rs` -> `parse_deposit_sign_session`
- Entrypoint: a gRPC request to the open port -> `parse_deposit_sign_session`
- Attacker controls: the request parameters selecting what is returned; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: obtain a commitment preimage or presigned transaction before it should be public
- Invariant to test: material returned by `parse_deposit_sign_session` is either already public on chain or useless to a non-participant
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: call `parse_deposit_sign_session` unauthenticated and assert no pre-reveal material is returned
