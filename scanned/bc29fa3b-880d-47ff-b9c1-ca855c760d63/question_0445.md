# Q0445: `verify_and_save_emergency_stop_sigs` and caller-specified transaction requests

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port submit a `TransactionRequest`-style message to `verify_and_save_emergency_stop_sigs` in `core/src/rpc/aggregator.rs` naming a deposit, round or kickoff they have no relation to, so an entity constructs and signs a bridge transaction for a context the attacker chose?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `verify_and_save_emergency_stop_sigs`
- Entrypoint: a gRPC request to the open port -> `verify_and_save_emergency_stop_sigs`
- Attacker controls: the deposit outpoint, round index, kickoff index and transaction type in the request; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: obtain a signed bridge transaction for an arbitrary context
- Invariant to test: the context a signed transaction is produced for == a context the protocol state machine selected
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: request signatures for an unrelated context and assert refusal
