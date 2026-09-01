# Q1980: `client` and protocol timelocks

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees place transactions so that a timelocked branch reached from `client` in `crates/clementine-extended-rpc/src/client.rs` becomes spendable before the protocol has taken its own action - filling blocks, delaying a dependency, or making a required transaction non-relayable - so a timeout branch claims a bridge UTXO the honest path was entitled to?

## Target
- File/function: `crates/clementine-extended-rpc/src/client.rs` -> `client` (Extended Bitcoin RPC client with retry logic)
- Entrypoint: Bitcoin transactions broadcast by an unprivileged party paying only mining fees around the timelock boundary -> `client`
- Attacker controls: the timing and relayability of the attacker's transactions; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: win a timeout branch against the honest path
- Invariant to test: the honest branch remains broadcastable and confirmable for the whole window before its timeout branch matures
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: regtest: run the window to its boundary and assert the honest branch still confirms
