The DEX pallet isn't in scope — the analog must live in crypto/* or networks/bitcoin. Let me check the bitcoin wallet scanner and output registration, where "smaller of two values" style mismatches could lock funds.### Title
`SignableTransaction::new` omits the `OP_RETURN` data output from the fee/weight calculation, producing under-priced transactions that will not relay or confirm - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a `data` payload is supplied, `SignableTransaction::new` appends an `OP_RETURN` output to `tx_outs` (send.rs:194-202), but computes the transaction weight and `needed_fee` from `payments` alone (send.rs:204, 226). The data output's weight (8-byte value + script, up to ~90 bytes for the 80-byte data cap checked at construction) is never paid for. The resulting transaction is signed and broadcast with an effective fee rate strictly below the caller-requested `fee_per_vbyte`, and potentially below the network minimum relay fee, so the transaction is rejected or stuck and the spent inputs' funds become unmovable/unspendable.

### Finding Description
Analogous to `buyShares()` minting shares from only the smaller of two deposited amounts while silently ignoring the excess, `SignableTransaction::new` computes the fee over only a subset of the transaction it actually builds:

1. `tx_outs` is populated with all payments **plus** the `OP_RETURN` output (send.rs:188-202).
2. `calculate_weight_vbytes` is called with `payments`, not `tx_outs` — the data output is excluded from the measured weight (send.rs:204).
3. `needed_fee = fee_per_vbyte * vbytes` therefore under-covers the real serialized size (send.rs:206), and the minimum-relay-fee check at send.rs:211 is evaluated against this understated size.
4. The change path repeats the mistake: `fee_with_change` is computed from `(payments, Some(&change))` — still excluding the `OP_RETURN` output (send.rs:225-227) — and the change amount is set to `input_sat - payment_sat - fee_with_change`, so the extra weight is absorbed into change, reducing the change output and the true fee rate.

The defect is reachable by an unprivileged party: `data` is caller-supplied transaction data (in Serai's usage, encoded InInstruction/Shorthand bytes attached to burns), so any user whose withdrawal embeds data produces a systematically under-priced transaction. Because signing commits to this exact transaction (`Prevouts::All`-style sighash over the built `tx`), the threshold validators sign an unintended (under-funded-fee) transaction the network may refuse.

### Impact Explanation
Every `SignableTransaction` carrying a `data` payload pays a lower effective sat/vbyte than requested. If the true rate drops below `DEFAULT_MIN_RELAY_TX_FEE`, the transaction is not relayed; the spent `ReceivedOutput` inputs are effectively frozen (the protocol treats them as consumed while the tx cannot confirm), mirroring the original report's "funds irretrievably locked" outcome. Even when relayed, confirmation is delayed far beyond the intended fee rate, and the change output is silently reduced by up to ~`90 * fee_per_vbyte` sats.

### Likelihood Explanation
Deterministic whenever `data.is_some()` — no race or adversarial timing required. The size check (81-byte cap) permits up to ~90 bytes of un-metered output, so the fee shortfall grows linearly with `fee_per_vbyte`; at elevated fee rates the under-payment is proportionally larger.

### Recommendation
In `SignableTransaction::new`, compute weight/vbytes over the actual outputs being committed — i.e., build the full `tx_outs` vector (payments + `OP_RETURN` + optional change) first, then measure it, or pass an explicit `has_data`/`data_len` into `calculate_weight_vbytes` so the `OP_RETURN` output is included in both the no-change and with-change fee computations (send.rs:204-235).

### Proof of Concept
```rust
// networks/bitcoin context: construct a SignableTransaction with a max-size
// OP_RETURN payload and observe the fee doesn't cover the data output.
let output: ReceivedOutput = /* scanned input worth input_sat */;
let data = vec![0u8; 80]; // passes the <=80-byte check
let tx = SignableTransaction::new(
    vec![output],
    &payments,
    Some(change_script), // change path also under-charged
    Some(data),
    fee_per_vbyte,
).unwrap();

// tx.needed_fee() == fee_per_vbyte * vbytes_without_opreturn_output
// Actual vsize includes the ~90-byte OP_RETURN output, so:
assert!(tx.needed_fee() < fee_per_vbyte * actual_vsize(&tx));
// Effective feerate = fee() / actual_vsize < fee_per_vbyte,
// possibly below DEFAULT_MIN_RELAY_TX_FEE -> not relayed, inputs frozen.
```
Concretely: `calculate_weight_vbytes` at send.rs:204 and 226 receives `payments` while `tx_outs` (which includes the pushed `OP_RETURN` `TxOut` at send.rs:195-201) is what the final `Transaction` at send.rs:246-251 actually commits to.