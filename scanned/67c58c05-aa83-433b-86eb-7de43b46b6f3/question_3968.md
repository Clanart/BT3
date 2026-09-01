# Q3968: `expected_msg_got_none` and disclosure of pre-reveal protocol material

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port call `expected_msg_got_none` in `core/src/rpc/error.rs` to obtain protocol material before its intended reveal - a Winternitz preimage, a challenge-ack preimage, an unbroadcast presigned transaction, an emergency-stop transaction - and use it to spend or invalidate a bridge UTXO?

## Target
- File/function: `core/src/rpc/error.rs` -> `expected_msg_got_none`
- Entrypoint: a gRPC request to the open port -> `expected_msg_got_none`
- Attacker controls: the request parameters selecting what is returned; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: obtain a commitment preimage or presigned transaction before it should be public
- Invariant to test: material returned by `expected_msg_got_none` is either already public on chain or useless to a non-participant
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: call `expected_msg_got_none` unauthenticated and assert no pre-reveal material is returned
