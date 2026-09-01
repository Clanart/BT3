# Q5834: `nonce_pair` may presign a vault for an output whose script set was never re-derived

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `nonce_pair` in `core/src/musig2.rs` with a deposit output whose taproot script tree differs from the one `DepositData::get_deposit_scripts` re-derives (extra leaf, reordered leaves, or a different internal key), and still have `Verifier::is_deposit_valid` accept it because only the aggregate `script_pubkey` is compared, so that N-of-N presigns a move-to-vault for an output the attacker can also spend by another path?

## Target
- File/function: `core/src/musig2.rs` -> `nonce_pair` (Helper functions for the MuSig2 signature scheme)
- Entrypoint: aggregator `NewDeposit` -> `Verifier::is_deposit_valid` -> `nonce_pair`
- Attacker controls: the full taproot script tree, leaf ordering, internal key, and the `DepositParams` actor lists sent to the aggregator; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: make the on-chain output's spend paths a strict superset of the ones the verifiers believe they are locking, then spend the deposit out from under the presigned move-to-vault
- Invariant to test: the set of spend paths of the funded deposit output == the set of spend paths in `get_deposit_scripts` for the accepted `DepositData`
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: construct the deposit output with an extra leaf, call the deposit flow in a regtest test, assert `is_deposit_valid` rejects it
