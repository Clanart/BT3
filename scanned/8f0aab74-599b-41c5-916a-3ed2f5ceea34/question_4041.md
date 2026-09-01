# Q4041: `start_jsonrpc_server` and Citrea-bound relaying

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees shape the data `start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` relays to or from Citrea (reveal scripts, serialized payloads, sync ranges) so the L2 record the bridge depends on differs from what the L1 transaction actually says?

## Target
- File/function: `crates/clementine-tx-sender/src/jsonrpc/server.rs` -> `start_jsonrpc_server`
- Entrypoint: attacker-shaped L1/L2 data -> `start_jsonrpc_server`
- Attacker controls: the payload bytes and the height ranges relayed; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: make L1 and L2 records of the same fact disagree
- Invariant to test: the record written on Citrea == the fact confirmed on Bitcoin
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: assert round-trip equality of relayed payloads for adversarial inputs
