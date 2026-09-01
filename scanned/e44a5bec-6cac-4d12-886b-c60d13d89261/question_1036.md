# Q1036: `get_fee_rate_from_mempool_space` and one-time connector outputs

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees cause a connector output tracked by `get_fee_rate_from_mempool_space` in `crates/clementine-extended-rpc/src/client.rs` to be consumed, burned or marked used without the corresponding protocol stage completing, so the deposit it belongs to loses its path to settlement?

## Target
- File/function: `crates/clementine-extended-rpc/src/client.rs` -> `get_fee_rate_from_mempool_space` (Extended Bitcoin RPC client with retry logic)
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `get_fee_rate_from_mempool_space`
- Attacker controls: the spend of the connector and its timing; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: burn the connector a legitimate settlement needed
- Invariant to test: a connector is marked used exactly when the stage it authorises actually completed
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: spend/observe the connector adversarially and assert bookkeeping matches the chain
