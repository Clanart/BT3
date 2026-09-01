# Q1582: `find_p2a_vout` and non-standard transaction handling

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees force a bridge transaction that `find_p2a_vout` in `crates/clementine-tx-sender/src/lib.rs` must send into a shape that policy rejects (size, sigops, dust, version, package limits), so it can never be relayed and the protocol stage it carries can never complete?

## Target
- File/function: `crates/clementine-tx-sender/src/lib.rs` -> `find_p2a_vout` (This crate handles the creation, signing, and broadcasting of Bitcoin transactions,)
- Entrypoint: attacker-shaped protocol state feeding transaction construction -> `find_p2a_vout`
- Attacker controls: the state that determines the transaction's size and shape; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: make a required bridge transaction permanently unrelayable
- Invariant to test: every bridge transaction the protocol must send is standard under default policy
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: assert `testmempoolaccept` passes for `find_p2a_vout`'s output across adversarial states
