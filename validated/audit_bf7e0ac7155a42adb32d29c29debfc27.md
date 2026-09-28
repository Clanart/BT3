### Title
`SignableTransaction::new` omits the OP_RETURN `data` output from fee/weight accounting, underpaying the intended fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the IDO report — where a documented allocation ratio was computed with the wrong split — `SignableTransaction::new` appends a caller-supplied OP_RETURN output to the transaction's outputs but excludes it from `calculate_weight_vbytes`. The transaction therefore underpays relative to the requested `fee_per_vbyte`, and the minimum-relay-fee sanity check is validated against an understated vbyte count.

### Finding Description
In `SignableTransaction::new`, untrusted `data` (up to 80 bytes, checked at line 171) is pushed as a real transaction output at lines 194-202:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

However, the weight/vbyte calculation at line 204 and the change branch at lines 225-226 both call `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)` using only `payments` — the `data` output is never included:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
...
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are computed for a smaller transaction than the one actually produced, so the real fee rate is lower than `fee_per_vbyte`.
2. The `TooLowFee` check at line 211 compares against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` with the understated `vbytes`, so a transaction whose true fee rate is below the minimum relay fee can be accepted and signed.
3. The change output (lines 228-234) is sized as `input_sat - payment_sat - fee_with_change`, so the shortfall is silently absorbed by paying a below-intended fee rather than being detected.

An OP_RETURN output carrying ~80 bytes of data adds roughly 90+ vbytes; the discrepancy is proportional to attacker-influenced `data.len()`.

### Impact Explanation
When `data` is supplied, the resulting signed transaction pays a lower effective fee rate than the caller specified. For `data` near the 80-byte bound, the understatement can push the real fee rate below `DEFAULT_MIN_RELAY_TX_FEE`, producing a transaction that nodes will not relay or that stalls in mempools — funds committed by the threshold group are then not spendable as intended until a replacement transaction is signed. This matches the report's class: a hardcoded accounting split (here, which outputs count toward fee sizing) deviates from the stated intent (`fee_per_vbyte` over the actual transaction), producing a concrete misallocation of value between outputs and fee.

### Likelihood Explanation
`data` is an untrusted, length-bounded byte vector supplied by the caller of `SignableTransaction::new` (used by `processor/src/networks/bitcoin.rs` when constructing outbound transactions, including OP_RETURN-carrying instructions). Any input that causes a transaction to be built with a non-trivial `data` payload deterministically triggers the undercount — no malformed encoding, race, or cooperation from other validators is required. The bug is deterministic in construction, not contingent on adversarial timing.

### Recommendation
Include the OP_RETURN output in the weight calculation. Either pass the fully built `tx_outs` (payments + data, then optionally change) into `calculate_weight_vbytes`, or add the data output's serialized size to the payments slice before computing `vbytes`. Apply the same fix to the change branch at line 226, and re-run the `TooLowFee` check against the final, complete output set so the minimum-relay-fee bound reflects the actual transaction.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics:
// Construct a SignableTransaction with a single 1_000_000-sat input,
// one payment, a change script, and data of 80 bytes.
let inputs = vec![received_output]; // ReceivedOutput with value = 1_000_000
let payments = &[(payment_script, 500_000u64)];
let data = Some(vec![0xaa; 80]);
let fee_per_vbyte = 10u64;

let stx = SignableTransaction::new(inputs, payments, Some(change_script), data, fee_per_vbyte)
  .unwrap();

// vbytes used internally excludes the OP_RETURN output (~90 vbytes).
// needed_fee() therefore under-reports by ~90 * fee_per_vbyte ≈ 900 sats,
// and fee() == needed_fee() (change absorbs the rest), so the real
// fee rate is ~10% below the requested fee_per_vbyte on a small tx —
// or below DEFAULT_MIN_RELAY_TX_FEE for a near-minimum payment.
assert!(stx.fee() == stx.needed_fee()); // fee < 10 * actual_vbytes
```

Cited code: `crypto` allocation site at `networks/bitcoin/src/wallet/send.rs:194-202`, the excluding weight call at `send.rs:204`, the change-branch recalculation at `send.rs:225-234`, and the min-fee check at `send.rs:206-213`. [1](#0-0) [2](#0-1) 

Uncertainty note: I confirmed the omission directly in `send.rs`; the exact upstream call site in `processor/src/networks/bitcoin.rs` that supplies `data` was located but its caller path (which protocol message feeds the OP_RETURN bytes) was not fully traced due to iteration limits — the reachability claim assumes the processor's transaction builder can emit transactions carrying caller-influenced instruction data, which is the documented purpose of the `data` parameter.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-213)
```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```
