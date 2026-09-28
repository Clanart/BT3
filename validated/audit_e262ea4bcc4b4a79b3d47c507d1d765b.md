### Title
`SignableTransaction::new` computes fee/vsize on a transaction missing the OP_RETURN output, underpaying the fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Sublime bug (a value denominated in one unit/universe — borrowAsset — being consumed in a context expecting another — shares), `SignableTransaction::new` computes `needed_fee` and the weight/size checks from a reconstructed transaction that only contains `payments`, while the actual signed transaction additionally includes the `data` OP_RETURN output. The weight/vbyte figures used for fee and size accounting therefore describe a *different transaction* than the one produced and signed.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` at send.rs:194-202, *before* the fee/size estimation call. However, `calculate_weight_vbytes` at send.rs:204 is called with `payments`, not `tx_outs`, and internally rebuilds the transaction from `payments` alone (send.rs:85-93):

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

The same omission occurs in the change branch (send.rs:225-226). An OP_RETURN output adds roughly `9 + 2 + data_len` bytes (~90+ weight units for the max 80-byte payload, plus compactsize/script overhead). Consequently:

- `needed_fee = fee_per_vbyte * vbytes` undercounts by the OP_RETURN's vsize, so the transaction pays a *lower effective fee rate* than the caller requested.
- The `TooLowFee` check at send.rs:211 is evaluated against the undercounted `vbytes`, so a transaction that is actually below `DEFAULT_MIN_RELAY_TX_FEE` can pass this check.
- The `MAX_STANDARD_TX_WEIGHT` check at send.rs:241 is evaluated against `weight` that omits the OP_RETURN output, so an actually-nonstandard transaction can be produced.

The resulting `SignableTransaction` is bound into the FROST signing session via `TransactionMachine`/`taproot_key_spend_signature_hash` (send.rs:373-391), so all signatures commit to the OP_RETURN-bearing transaction whose fee was priced without it.

### Impact Explanation
A produced and threshold-signed transaction can carry a fee below the intended `fee_per_vbyte`, and potentially below the default minimum relay fee, making it unrelayable/unconfirmable as constructed. Since signatures commit to the exact transaction (SIGHASH_DEFAULT commits to all outputs via `Prevouts::All`), the fee cannot be bumped without redoing the signing protocol with a corrected `SignableTransaction` — burning a round of threshold signing and potentially stalling a withdrawal. In the weight-check case, a transaction exceeding standardness limits can be signed and will be rejected by relay. Funds are not directly stealable, but outputs committed to such transactions are temporarily unspendable (medium severity: funds reported/processed through a path that produces an unusable transaction).

### Likelihood Explanation
Triggered whenever `SignableTransaction::new` is called with `data = Some(..)` and the input surplus is small relative to the true fee, or when `fee_per_vbyte` is at/near the minimum relay threshold. The `data` parameter is external-facing transaction-construction input, reachable by whoever drives the wallet to attach OP_RETURN data (e.g., event/metadata payloads). It deterministically underprices every data-bearing transaction; whether it causes relay failure depends on margin.

### Recommendation
Build `tx_outs` (payments + OP_RETURN + change) first and pass the actual output list into `calculate_weight_vbytes`, or pass `data` into it so the reconstructed transaction matches the signed one. Concretely, change `calculate_weight_vbytes` to accept the full `&[TxOut]` (or payments plus `data: Option<&[u8]>` and `change`), and re-run the `TooLowFee` and `MAX_STANDARD_TX_WEIGHT` checks against the final output set.

### Proof of Concept
```rust
// With inputs summing to `payment + rate * vbytes_no_opreturn + epsilon`,
// and data = Some(vec![0; 80]):
let stx = SignableTransaction::new(
  inputs,                          // one ReceivedOutput
  &[(payment_script, 10_000)],
  None,
  Some(vec![0u8; 80]),             // OP_RETURN ~91 bytes not priced in
  1,                               // sat/vbyte
).unwrap();
// stx.fee() == inputs - outputs, computed from vbytes that exclude the OP_RETURN
// Effective rate = fee / actual_vbytes < 1 sat/vbyte, and possibly
// fee < DEFAULT_MIN_RELAY_TX_FEE * actual_vbytes / 1000 → unrelayable,
// despite TooLowFee passing on the smaller vbytes.
```