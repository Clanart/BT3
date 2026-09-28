### Title
`SignableTransaction::new` computes fee/weight without the OP_RETURN output, so the actual fee rate slips below the caller-specified `fee_per_vbyte` — potentially below the minimum relay fee — with no bound enforcement - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the SymmIO finding (order executes at a price worse than the user's implicit bound, forcing cancellation and re-submission), `SignableTransaction::new` accepts a `fee_per_vbyte` bound from the caller but calculates the transaction's weight/vbytes from `payments` only, before the OP_RETURN `data` output is appended. The signed transaction therefore realizes a lower effective fee rate than the caller specified, and there is no slippage-style check that the realized rate still meets the intended bound.

### Finding Description
The caller supplies `data: Option<Vec<u8>>` (up to 80 bytes) and `fee_per_vbyte` to `SignableTransaction::new`. The OP_RETURN output is pushed onto `tx_outs` at lines 194–202, but `calculate_weight_vbytes` is invoked at line 204 with only `tx_ins.len()` and `payments`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

Inside `calculate_weight_vbytes` (lines 85–93), `tx.output` is built solely from `payments`, so the serialized size of the OP_RETURN output (8-byte value + script length + `OP_RETURN` + push of up to 80 bytes, roughly 84–93 vbytes) is never counted. Both `needed_fee` and the minimum-relay-fee check at line 211 use this underestimated `vbytes`:

```rust
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

The same underestimate occurs in the change branch: `vbytes_with_change` at line 226 also excludes the data output, so `fee_with_change` and the change leftover value are computed against a too-small size. Additionally, when change is added, `needed_fee = fee_with_change` but the actual fee becomes `input_sat - payment_sat - change_value`, which equals `fee_with_change` only by the underestimated vbytes.

Concretely: with `fee_per_vbyte = 1` (exactly `DEFAULT_MIN_RELAY_TX_FEE`), `needed_fee = vbytes` passes the check, but the real transaction is ~`vbytes + 93` vbytes, giving an effective rate of `vbytes/(vbytes+93) < 1 sat/vB` — below the minimum relay fee. The transaction is produced, signed via `TransactionSignMachine::sign` (lines 373–397, which faithfully signs `taproot_key_spend_signature_hash` over the real, larger outputs), and the resulting Bitcoin transaction will not propagate on the default relay policy. The caller's specified bound was silently violated — the exact "no slippage parameter / bound not enforced" defect class.

### Impact Explanation
An unprivileged caller of `SignableTransaction::new` (public API, public inputs `data` and `fee_per_vbyte`) can produce a threshold-signed Bitcoin transaction that pays a fee below the minimum relay rate. The outputs are then unspendable in practice: the transaction cannot be broadcast, and the spent UTXOs are locked until a new signing session is run with corrected parameters — mirroring the original report's "user must cancel and resubmit at worse terms" impact, here with FROST signing-round costs. Even when relayable, every transaction carrying `data` pays a strictly lower effective fee rate than requested, a silent violation of the caller's intent with no bound check anywhere in `new` or `fee()`.

### Likelihood Explanation
Triggered deterministically whenever `data.is_some()` — no attacker cooperation needed, only the public `new` API. The severity is bounded (fee shortfall is capped at the size of one ≤80-byte OP_RETURN output, ~93 vbytes), keeping it Medium: it causes a stuck/unbroadcastable transaction at boundary fee rates and an unintended fee outcome at all rates, but cannot redirect funds or leak key material.

### Recommendation
Include the OP_RETURN output in the weight calculation: pass the final output set (or the data length) into `calculate_weight_vbytes`, or add the data output's size to `weight`/`vbytes` before computing `needed_fee`. Apply the same correction in the `change` branch so `vbytes_with_change` reflects the real transaction. Optionally expose the realized fee rate via `fee()`/`needed_fee()` so callers can enforce a maximum slippage bound before signing.

### Proof of Concept
```rust
// networks/bitcoin context: one funded ReceivedOutput, one payment, 80-byte data
let inputs = vec![funded_output]; // value: payment + vbytes + margin
let payments = vec![(dest_script, payment_amount)];
let data = Some(vec![0xaa; 80]);

// Request the minimum viable rate: 1 sat/vB
let tx = SignableTransaction::new(inputs, &payments, None, data, 1).unwrap();

// vbytes was computed WITHOUT the OP_RETURN output
// tx.fee() == vbytes (sats), but actual tx is ~vbytes + 93 vbytes
// effective rate = vbytes / (vbytes + 93) < 1 sat/vB
// -> below DEFAULT_MIN_RELAY_TX_FEE; signed tx will not relay,
//    locking the inputs until a full re-sign
```