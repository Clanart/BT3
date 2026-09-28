### Title
`SignableTransaction::new` computes the transaction weight, virtual size, required fee, and maximum-weight check on a smaller set of outputs than it actually creates — the OP_RETURN `data` output is excluded — so the real transaction pays a lower effective fee rate (and may be nonstandard) than accounted for. - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the PartyDAO `totalVotingPower` bug — where the aggregate total was computed on the gross contribution while each user's share was computed on the net amount, making the sum of parts permanently unable to reach the total — `SignableTransaction` computes its "total" (weight/vbytes/`needed_fee`) on `payments` only, while the transaction actually broadcast contains `payments + OP_RETURN data + change`. The accounting total never reflects the OP_RETURN output, so every check that relies on it is wrong in the same direction.

### Finding Description
In `SignableTransaction::new`, the `data` output is pushed onto `tx_outs` *before* the weight calculation, but `calculate_weight_vbytes` is invoked with `payments` — not `tx_outs` — so the OP_RETURN output (up to 80 bytes of payload plus output overhead) is never included:

```rust
// networks/bitcoin/src/wallet/send.rs
// OP_RETURN is added to tx_outs here (lines 194-202):
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}

// but weight/vbytes are computed from `payments`, not `tx_outs` (line 204):
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` (lines 62-127) builds its measurement transaction solely from `payments` plus an optional `change` output — there is no parameter for the data output, and the change recalculation at lines 224-235 suffers the same omission:

```rust
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
let fee_with_change = fee_per_vbyte * vbytes_with_change;
```

Two downstream checks therefore operate on an understated total:

1. `needed_fee` / `fee_with_change` are calibrated to a vbyte count that omits the OP_RETURN output. Since the fee actually paid equals `input_sat - payment_sat - change` (change is sized to make the fee exactly `fee_with_change`), the mined transaction's *effective* fee rate is lower than `fee_per_vbyte`. The minimum-fee guard at lines 211-213 (`TooLowFee`) is also evaluated against the understated vbytes, so a transaction whose true fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` passes the check and is signed anyway.
2. The `MAX_STANDARD_TX_WEIGHT` check at lines 241-243 uses `weight`, which never includes the OP_RETURN output, so an oversized (nonstandard) transaction is accepted.

### Impact Explanation
The signed transaction is nonstandard or under-priced: it will be rejected by default relay policy (`minrelaytxfee`) or never confirm, while the UTXOs it references have had a signed spend produced for them. Funds are effectively locked in an unbroadcastable/unconfirmable transaction — matching the accepted analog criterion of funds accounted for in a way that makes them not spendable under the produced signature. A respend requires an entirely new threshold signing round with corrected inputs, and any retry constructed identically reproduces the failure.

### Likelihood Explanation
Triggered deterministically whenever `data` is `Some` — there is no edge-case rounding required; the OP_RETURN output (~10 + payload bytes of weight, plus an output record) is *always* omitted. Whether it crosses the relay/standardness threshold depends on `fee_per_vbyte`, output sizes, and proximity to `MAX_STANDARD_TX_WEIGHT`, but the fee-rate understatement itself occurs on every data-carrying transaction.

### Recommendation
Build the measurement transaction from the actual output list. Pass the full `tx_outs` (or `payments` plus `data`) into `calculate_weight_vbytes` for both the initial `needed_fee`/`TooLowFee` check and the `fee_with_change` recomputation, and use the resulting true `weight` for the `MAX_STANDARD_TX_WEIGHT` check — i.e., compute the total from the same set of parts that will be spent, mirroring the PartyDAO fix of computing `newVotingPower` from `totalContributions_` *after* the fee deduction.

### Proof of Concept
```rust
// Conceptual: construct a SignableTransaction whose data output is never weighed.
let data = vec![0u8; 80]; // maximum allowed by the TooMuchData check
let stx = SignableTransaction::new(
    inputs, payments, Some(change_script), Some(data), fee_per_vbyte,
).unwrap();

// stx.transaction() contains payments.len() + 2 outputs (payments + OP_RETURN + change),
// yet needed_fee() and the internal weight were computed for payments.len() + 1 outputs.
// stx.transaction().weight() > the weight used for MAX_STANDARD_TX_WEIGHT, and
// stx.fee() / real_vbytes < fee_per_vbyte.
//
// With fee_per_vbyte at/below the point where the omitted OP_RETURN weight pushes the
// effective rate under DEFAULT_MIN_RELAY_TX_FEE, the TooLowFee check passes but the
// signed transaction is rejected by relay policy: inputs accounted as spent, tx unspendable.
```

Caveat: whether `data` is reachable by an unprivileged caller depends on the integrator path that invokes `SignableTransaction::new`; within `bitcoin-serai` itself the inconsistency between the accounted total and the created outputs is unconditional whenever `data.is_some()`.