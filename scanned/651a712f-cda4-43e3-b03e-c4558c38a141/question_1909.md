# Q1909: `deposit_sign` as an arbitrary-transaction broadcast primitive

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port use `deposit_sign` in `core/src/rpc/operator.rs` to make a bridge entity sign, fund, fee-bump or broadcast a transaction of the attacker's choosing - or to broadcast a bridge transaction at a moment of the attacker's choosing - so a protocol transaction is published out of order or a bridge-controlled UTXO is spent early?

## Target
- File/function: `core/src/rpc/operator.rs` -> `deposit_sign`
- Entrypoint: a gRPC request to the open port -> `deposit_sign`
- Attacker controls: the raw transaction or transaction request in the message body; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: turn a bridge entity into a signing or broadcasting oracle
- Invariant to test: every transaction an entity broadcasts is one the protocol state machine decided to send
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: call `deposit_sign` with an arbitrary raw tx and assert it is refused or strictly validated
