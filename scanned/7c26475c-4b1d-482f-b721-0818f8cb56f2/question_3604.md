# Q3604: `get_cached_tx` and key-path vs script-path tweaking

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator construct a deposit whose taproot output makes `get_cached_tx` in `core/src/builder/transaction/txhandler.rs` apply the wrong tweak (untweaked key-path where a merkle root is required, or a `TapNodeHash` supplied by the caller), so an N-of-N signature valid for the tweaked key is also valid for a key the attacker controls, or vice versa?

## Target
- File/function: `core/src/builder/transaction/txhandler.rs` -> `get_cached_tx` (This module defines the [`TxHandler`] abstraction, which wraps a protocol transaction and its metadata)
- Entrypoint: aggregator `NewDeposit` -> `Actor` signing helpers -> `get_cached_tx`
- Attacker controls: the deposit output's internal key and merkle root, and any `TapNodeHash` carried in the request; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: reuse a bridge signature under a key the attacker can spend from
- Invariant to test: the tweak applied when signing == the tweak committed by the output being spent
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: unit-test `get_cached_tx` with a caller-supplied merkle root and assert the signature does not verify for the untweaked key
