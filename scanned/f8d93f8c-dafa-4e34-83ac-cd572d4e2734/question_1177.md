# Q1177: `number_of_flattened_wpks` does not require the deposit outpoint to still be unspent

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `number_of_flattened_wpks` in `core/src/bitvm_client.rs` with a `deposit_outpoint` that is already spent (or is replaced by RBF between the verifiers' `get_tx_of_txid` check and the move-to-vault broadcast), so the verifiers presign and the aggregator publishes a move-to-vault that can never confirm while the bridge accounting already records the deposit?

## Target
- File/function: `core/src/bitvm_client.rs` -> `number_of_flattened_wpks`
- Entrypoint: aggregator `NewDeposit` -> `number_of_flattened_wpks` -> `Aggregator::create_movetx`
- Attacker controls: the deposit transaction, its replaceability, and a conflicting spend of the same outpoint; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: get a deposit recorded whose vault UTXO does not and cannot exist
- Invariant to test: a move txid recorded as a deposit == a transaction whose input is a confirmed unspent deposit output
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: double-spend the deposit outpoint mid-round and assert no deposit is recorded
