# Q5762: `get_verifiers` does not require the deposit outpoint to still be unspent

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `get_verifiers` in `core/src/deposit.rs` with a `deposit_outpoint` that is already spent (or is replaced by RBF between the verifiers' `get_tx_of_txid` check and the move-to-vault broadcast), so the verifiers presign and the aggregator publishes a move-to-vault that can never confirm while the bridge accounting already records the deposit?

## Target
- File/function: `core/src/deposit.rs` -> `get_verifiers` (This module defines the data structures related to Citrea deposits in the Clementine bridge)
- Entrypoint: aggregator `NewDeposit` -> `get_verifiers` -> `Aggregator::create_movetx`
- Attacker controls: the deposit transaction, its replaceability, and a conflicting spend of the same outpoint; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: get a deposit recorded whose vault UTXO does not and cannot exist
- Invariant to test: a move txid recorded as a deposit == a transaction whose input is a confirmed unspent deposit output
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: double-spend the deposit outpoint mid-round and assert no deposit is recorded
