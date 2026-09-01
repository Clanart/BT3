# Q2399: `get_latest_proven_block_info` and overwriting an established mapping

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port cause `get_latest_proven_block_info` in `core/src/database/header_chain_prover.rs` to overwrite an already-established mapping (an index to a move txid, a withdrawal to a UTXO, a payout to a party) with a later value derived from attacker-influenced data, so the bridge later acts on the replaced value?

## Target
- File/function: `core/src/database/header_chain_prover.rs` -> `get_latest_proven_block_info` (This module includes database functions which are mainly used by the header)
- Entrypoint: attacker-influenced Citrea or Bitcoin data -> `get_latest_proven_block_info`
- Attacker controls: the data that produces the later write; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: rewrite settled bridge bookkeeping
- Invariant to test: an established mapping is immutable once any protocol action has relied on it
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: attempt the overwrite and assert `get_latest_proven_block_info` refuses or is append-only
