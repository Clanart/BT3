# Q0759: `decode` and overwriting an established mapping

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port cause `decode` in `core/src/database/wrapper.rs` to overwrite an already-established mapping (an index to a move txid, a withdrawal to a UTXO, a payout to a party) with a later value derived from attacker-influenced data, so the bridge later acts on the replaced value?

## Target
- File/function: `core/src/database/wrapper.rs` -> `decode` (This module includes wrappers for easy parsing of the foreign types)
- Entrypoint: attacker-influenced Citrea or Bitcoin data -> `decode`
- Attacker controls: the data that produces the later write; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: rewrite settled bridge bookkeeping
- Invariant to test: an established mapping is immutable once any protocol action has relied on it
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: attempt the overwrite and assert `decode` refuses or is append-only
