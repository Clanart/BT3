# Q0799: `check_if_utxo_spending_tx_is_finalized` and overwriting an established mapping

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port cause `check_if_utxo_spending_tx_is_finalized` in `core/src/database/bitcoin_syncer.rs` to overwrite an already-established mapping (an index to a move txid, a withdrawal to a UTXO, a payout to a party) with a later value derived from attacker-influenced data, so the bridge later acts on the replaced value?

## Target
- File/function: `core/src/database/bitcoin_syncer.rs` -> `check_if_utxo_spending_tx_is_finalized`
- Entrypoint: attacker-influenced Citrea or Bitcoin data -> `check_if_utxo_spending_tx_is_finalized`
- Attacker controls: the data that produces the later write; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: rewrite settled bridge bookkeeping
- Invariant to test: an established mapping is immutable once any protocol action has relied on it
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: attempt the overwrite and assert `check_if_utxo_spending_tx_is_finalized` refuses or is append-only
