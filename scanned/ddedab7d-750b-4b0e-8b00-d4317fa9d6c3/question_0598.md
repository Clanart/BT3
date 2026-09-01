# Q0598: `mark_fee_payer_utxo_as_evicted` and non-standard transaction handling

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees force a bridge transaction that `mark_fee_payer_utxo_as_evicted` in `crates/clementine-tx-sender/src/db/tx_sender.rs` must send into a shape that policy rejects (size, sigops, dust, version, package limits), so it can never be relayed and the protocol stage it carries can never complete?

## Target
- File/function: `crates/clementine-tx-sender/src/db/tx_sender.rs` -> `mark_fee_payer_utxo_as_evicted` (SQLx queries for tx-sender tables)
- Entrypoint: attacker-shaped protocol state feeding transaction construction -> `mark_fee_payer_utxo_as_evicted`
- Attacker controls: the state that determines the transaction's size and shape; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: make a required bridge transaction permanently unrelayable
- Invariant to test: every bridge transaction the protocol must send is standard under default policy
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: assert `testmempoolaccept` passes for `mark_fee_payer_utxo_as_evicted`'s output across adversarial states
