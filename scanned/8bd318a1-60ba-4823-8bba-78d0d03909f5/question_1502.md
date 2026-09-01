# Q1502: `delete_try_to_send_tx` and non-standard transaction handling

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees force a bridge transaction that `delete_try_to_send_tx` in `crates/clementine-tx-sender/src/db/tx_sender.rs` must send into a shape that policy rejects (size, sigops, dust, version, package limits), so it can never be relayed and the protocol stage it carries can never complete?

## Target
- File/function: `crates/clementine-tx-sender/src/db/tx_sender.rs` -> `delete_try_to_send_tx` (SQLx queries for tx-sender tables)
- Entrypoint: attacker-shaped protocol state feeding transaction construction -> `delete_try_to_send_tx`
- Attacker controls: the state that determines the transaction's size and shape; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: make a required bridge transaction permanently unrelayable
- Invariant to test: every bridge transaction the protocol must send is standard under default policy
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: assert `testmempoolaccept` passes for `delete_try_to_send_tx`'s output across adversarial states
