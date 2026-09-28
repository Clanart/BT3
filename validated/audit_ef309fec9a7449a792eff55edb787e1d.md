### Title
Scanner accepts zero-value outputs as spendable inputs, letting anyone poison the wallet's UTXO set with economically-unspendable "funds" - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` registers any output whose `script_pubkey` matches a watched script, with no check on `output.value`. A zero-value (or 1-satoshi) P2TR output is consensus-valid even though it is non-standard under relay policy, so an unprivileged party can craft a transaction paying a 0-value output to a Serai key. The scanner reports it as a `ReceivedOutput`, and `SignableTransaction::new` will later include it as an input — which contributes zero sats while still costing ~57 vbytes of fee. This mirrors the reported bug class (a zero amount silently corrupts the batch): one zero-value output degrades every transaction it is pulled into.

### Finding Description
`scan_transaction` iterates transaction outputs and pushes a `ReceivedOutput` for any matching `script_pubkey` without inspecting the amount (networks/bitcoin/src/wallet/mod.rs:199-214). `SignableTransaction::new` then sums all input values into `input_sat` (networks/bitcoin/src/wallet/send.rs:175) and builds a `TxIn` for every input with no dust/minimum-value filter on inputs — the `DUST` check is only applied to outgoing payments (send.rs:165-169) and to the change output (send.rs:228-233). The fee accounting (`fee()` at send.rs:138-141) simply computes `sum(prevouts) - sum(outputs)`, so a zero-value input is a pure liability: it adds weight (raising `needed_fee` via `calculate_weight_vbytes`, send.rs:62-99) and contributes nothing. Worse, `Scanner` also scans coinbase transactions (mod.rs:221-227), compounding the reachability. The result is "funds reported received that are not spendable" — a zero-value output can never be spent without burning more in fees than it returns, and it can even push a marginal transaction into `NotEnoughFunds` (send.rs:215-221), stalling legitimate payments — the same "one zero poisons the whole distribution" failure shape as the FeeSplitter report.

### Impact Explanation
An attacker can permanently inflate the wallet's UTXO set with zero-value outputs that the scanner reports as received. Each such input either (a) is never spent, leaving reported-but-worthless funds, or (b) is included in a `SignableTransaction`, where it nets a negative contribution — it costs fee weight while adding 0 sats, silently draining the multisig's balance or causing `NotEnoughFunds` failures that block otherwise-valid payments (a DoS of the whole batch, exactly as a zero `proportion` reverts `distributeFees` for all recipients).

### Likelihood Explanation
A zero-value output is consensus-valid; dust is only a mempool relay rule. Any party can send one to the Serai address by arranging mining (e.g., via direct miner submission, which is routine), at the cost of a transaction fee. From then on the poisoned input persists until spent, and every transaction that pulls it in is degraded — an unprivileged, one-time public input causing ongoing harm.

### Recommendation
Apply a minimum-value check in `Scanner::scan_transaction` (or when selecting inputs in `SignableTransaction::new`) so that outputs whose value is below the cost to spend them (analogous to the `DUST` constant) are never reported as `ReceivedOutput` — the same fix shape as the report's recommendation to add a zero check in `setFeeRecipients`.

### Proof of Concept
1. Compute the P2TR script for the group key via `p2tr_script_buf(key)` (mod.rs:80-86).
2. Craft a transaction with `TxOut { value: Amount::ZERO, script_pubkey }` and get it mined (consensus-valid; only non-standard to relay).
3. `Scanner::scan_block`/`scan_transaction` returns a `ReceivedOutput` with `value() == 0` (mod.rs:199-214).
4. `SignableTransaction::new` includes it among `tx_ins`, increasing `needed_fee` while `input_sat` gains nothing; for a marginal plan this yields `Err(NotEnoughFunds)` (send.rs:215-221), or, when funded, burns the input's ~57-vbyte cost from the multisig — funds the scanner reported received that are not spendable.