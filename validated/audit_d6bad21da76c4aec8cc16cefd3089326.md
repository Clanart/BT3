### Title
`SignableTransaction::new` omits the OP_RETURN data output from the fee/weight calculation, underpaying the fee on the signed transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
This is the Serai analog of "a value already scaled/measured on one quantity is divided (or here, multiplied) against a different, wrong quantity". In GMX, `getFundingFeeAmount` computed `adjustedPositionSizeInUsd` already divided by `FLOAT_PRECISION_SQRT` and then divided the numerator by `FLOAT_PRECISION` again, producing an amount wrong by a factor of `FLOAT_PRECISION_SQRT`. In `bitcoin-serai`, the analogous unit mismatch is between the *real* transaction and the *synthetic* transaction used to measure vbytes: `SignableTransaction::new` pushes the caller-supplied OP_RETURN output into `tx_outs` (the real tx) but calls `calculate_weight_vbytes` with only `payments` and `change`, so the data output's vbytes are never counted in `needed_fee` — the fee is computed against the wrong (smaller) transaction size.

### Finding Description
`SignableTransaction::new` builds the real output list `tx_outs` including the OP_RETURN data output when `data` is `Some` (`send.rs:193-202`). However, fee sizing calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` (`send.rs:204`) and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (`send.rs:225-226`), and `calculate_weight_vbytes` constructs its measurement transaction's outputs solely from `payments` and `change` (`send.rs:85-99`). `payments` here is the caller's `&[(ScriptBuf, u64)]` slice — the OP_RETURN output added to `tx_outs` is not represented.

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) and `fee_with_change` (`send.rs:227`) are computed on a transaction missing the OP_RETURN output (~10–91 vbytes for up to 80 bytes of data), so `needed_fee` under-reports the fee actually needed for the requested rate. `needed_fee()` is the value integrators/processors use to account for fees, so it is simply wrong — the same class as the GMX report where the returned amount is computed against the wrong precision factor.
2. The minimum-relay-fee guard (`send.rs:211`) compares `fee_per_vbyte * vbytes` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` — `vbytes` cancels, so the check cannot catch the shortfall. A transaction built with `fee_per_vbyte` at the relay minimum plus a large `data` payload will actually pay below the minimum relay fee on its true vsize.
3. When `change` is present, `change = input_sat - payment_sat - fee_with_change` (`send.rs:228-230`) inherits the too-small `fee_with_change`, so the change output is inflated and the effective fee rate on the real (larger) transaction drops below `fee_per_vbyte`.

### Impact Explanation
Any caller that passes `data` produces a signed Taproot transaction whose actual sat/vbyte rate is lower than requested — potentially below Bitcoin's default minimum relay fee, making the transaction un-relayable/un-confirmable and the spent inputs' funds effectively frozen until a replacement is arranged. `needed_fee()` returns a value inconsistent with the transaction's true size, so downstream accounting relying on it misstates the fee. Reachable by any party able to cause a transaction with an OP_RETURN `data` payload to be constructed (data of up to 80 bytes is accepted at `send.rs:171-173` and `send.rs:195`).

### Likelihood Explanation
Triggering requires only that a transaction be built with non-empty `data`; the miscalculation then occurs deterministically on every such construction. Whether `data` is attacker-influenced depends on the integrator, but the miscounting is unconditional once `data` is supplied — no race, no collusion.

### Recommendation
Include the OP_RETURN output in the weight measurement. Either pass the fully-formed `tx_outs` into `calculate_weight_vbytes`, or add a `data: Option<&[u8]>` parameter and push the same `TxOut` inside `calculate_weight_vbytes` so the measured transaction matches the real one. Recompute `needed_fee`/`fee_with_change` (and the `TooLowFee` check) against the true vbytes including the data output.

### Proof of Concept
In `SignableTransaction::new`:

```rust
// tx_outs gets the OP_RETURN output (send.rs:193-202)
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

// but the fee is measured on a tx WITHOUT it (send.rs:204)
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` only ever places `payments` and `change` in the synthetic tx (`send.rs:85-99`). Construct a `SignableTransaction` with `data = Some(vec![0u8; 80])`, `change = Some(..)`, and `fee_per_vbyte = 1`. The returned `needed_fee()` equals `1 * vbytes(without OP_RETURN)`, while `tx.vsize()` is ~90 vbytes larger; `fee() / vsize` on the signed transaction is therefore < 1 sat/vbyte — below `DEFAULT_MIN_RELAY_TX_FEE` — even though the `TooLowFee` check passed. Compare with the analogous call at `send.rs:225-226` which repeats the same omission when sizing the fee with change.