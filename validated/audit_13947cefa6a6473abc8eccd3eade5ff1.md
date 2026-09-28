### Title
`SignableTransaction::new` validates fee and weight against a transaction missing the OP_RETURN output — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` builds the real `tx_outs` (including a caller-supplied OP_RETURN `data` output of up to 80 bytes) but computes `weight`, `vbytes`, `needed_fee`, the `TooLowFee` bound, the change amount, and the `TooLargeTransaction` check from `calculate_weight_vbytes(inputs, payments, change)` — which reconstructs a transaction containing only `payments` and `change`, omitting the data output entirely. Like the Piwigo `url_check_format` bypass, an intended restriction (minimum fee rate, standardness weight cap) is checked against a different, smaller object than the one actually produced.

### Finding Description
At `send.rs:194-202` the OP_RETURN output is appended to `tx_outs`. At `send.rs:204` and `send.rs:225-226` the weight/vbytes used for `needed_fee`, `TooLowFee` (`send.rs:211`), change (`send.rs:228-233`), and `TooLargeTransaction` (`send.rs:241`) are all derived from `payments`/`change` only — the data output is never included:

- `needed_fee = fee_per_vbyte * vbytes` where `vbytes` excludes the OP_RETURN output (~9 + 80 bytes ≈ 90+ non-witness bytes ≈ ~90 vbytes). The actual fee rate of the signed transaction is therefore materially below `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE` per actual vbyte even though the `TooLowFee` check passed — producing a transaction Bitcoin nodes will not relay.
- `weight > MAX_STANDARD_TX_WEIGHT` is checked on the payments-only weight (`send.rs:241`), so a transaction that is non-standard (and thus unbroadcastable) once the data output is included still passes validation.
- Change is computed as `input_sat - payment_sat - fee_with_change` (`send.rs:228`), so the change output absorbs the fee shortfall; `fee()` (`send.rs:139`) confirms the realized fee equals the under-computed `needed_fee`.

`data` is untrusted, user-supplied input: it originates from `OutInstruction.data`, i.e., any unprivileged user performing a burn/withdrawal with attached data reaches this path via `bitcoin_serai::wallet::SignableTransaction::new`.

### Impact Explanation
A user-supplied `data` field causes the multisig to sign a Bitcoin transaction whose effective fee rate is lower than the configured `fee_per_vbyte` — potentially below relay minimums — or whose true weight exceeds `MAX_STANDARD_TX_WEIGHT`, making it non-standard. The coordinator will sign and attempt to broadcast a transaction that cannot propagate, stalling/burning the withdrawal flow and requiring recovery, and in the change case silently mis-accounting the change amount relative to the intended fee policy. This is a validation-vs-construction mismatch on attacker-influenced input, matching the "insufficient format check bypasses intended restriction" class of CVE-2016-10514. Severity: Medium.

### Likelihood Explanation
Any out-instruction carrying data (up to 80 bytes, `send.rs:171`) triggers the discrepancy deterministically; no collusion or privileged position is needed. Whether the resulting transaction is actually rejected depends on how close `fee_per_vbyte` is to relay minimums and how close the payments-only weight is to the standardness cap, but the fee underpayment occurs on every data-bearing transaction.

### Recommendation
Include the OP_RETURN output in the size calculation: pass the full `tx_outs` (or `payments` plus a synthetic data output) to `calculate_weight_vbytes` — e.g., change the signature to take `tx_outs` directly, or append `TxOut { value: Amount::ZERO, script_pubkey: <the OP_RETURN script> }` to the template before computing `weight`/`vbytes` — for both the no-change and with-change calls, and perform the `MAX_STANDARD_TX_WEIGHT` check on the final assembled `tx` itself.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics
let inputs = vec![received_output];            // value V
let payments = &[(payment_script, DUST)];      // one dust payment
let data = Some(vec![0u8; 80]);                // max allowed
let tx = SignableTransaction::new(inputs, payments, None, data, fee_per_vbyte).unwrap();

// tx.needed_fee() == fee_per_vbyte * vbytes(payments-only template)
// tx.transaction().vsize() includes the OP_RETURN output (~93 extra bytes)
assert!(tx.fee() < fee_per_vbyte * u64::try_from(tx.transaction().vsize()).unwrap());
// i.e. the realized fee rate is strictly below the requested fee_per_vbyte,
// despite the TooLowFee guard, because vbytes omitted the data output.

// Standardness bypass: choose payments so the payments-only weight == MAX_STANDARD_TX_WEIGHT;
// the appended OP_RETURN output pushes the real tx over the cap while line 241 still passes.
```