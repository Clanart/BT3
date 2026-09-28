### Title
Fee/weight calculation omits the OP_RETURN data output, undercharging fees and producing transactions below the intended (and possibly relay-minimum) feerate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes the transaction weight (and therefore `needed_fee`) from `payments` only, even though an OP_RETURN output carrying up to 80 bytes of caller-supplied `data` is appended to the real transaction's outputs before the weight check. The analog to "fees charged on the theoretical amount instead of the actual amount" is inverted but the same root cause: the fee is computed on a theoretical transaction shape that differs from the transaction actually built and signed.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the OP_RETURN output is pushed onto `tx_outs` at lines 194–202, but `needed_fee` is derived from `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, which builds its weight-estimation `Transaction` solely from `payments` (lines 85–93) and never includes the data output. The same omission occurs in the change path at line 226 (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`). With `data` up to 80 bytes (checked at line 171), the real transaction can be ~90+ vbytes larger than estimated, so `fee = input_sat - payment_sat - change` equals `needed_fee` for a smaller transaction than what is broadcast. [1](#0-0) [2](#0-1) 

### Impact Explanation
The signed transaction pays `fee_per_vbyte * vbytes(no-data-tx)` while occupying `vbytes(tx-with-OP_RETURN)`, so its effective feerate is always below the caller-requested rate. If `fee_per_vbyte` is at/near the relay minimum, the min-relay check at line 211 uses the understated vbytes too, so the transaction may pass the check yet be rejected by peers or sit unconfirmed, stalling the funds the signed inputs represent. In a threshold-wallet flow, this means a completed signing round yields an unrelayable transaction, requiring a fresh sign.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is called with `Some(data)`; `data` is a caller-controlled public input (up to 80 bytes, line 171). The bug is deterministic — every call with data undercounts weight — so any low-feerate environment or near-minimum `fee_per_vbyte` triggers underpayment. Notably, the in-repo processor caller passes `None` for data (`processor/src/networks/bitcoin.rs:450`), limiting practical exposure to integrators that supply data; impact is Medium (stuck/unrelayable TX, wasted signing round) rather than direct fund loss.

### Recommendation
Include the OP_RETURN output in the weight estimate — e.g., pass the fully-constructed `tx_outs` (or `payments` plus the data output) into `calculate_weight_vbytes` — in both the no-change and with-change calculations, so `needed_fee` covers the actual serialized size.

### Proof of Concept
```rust
// With a single 1 BTC input, one payment, and 80 bytes of data:
let tx = SignableTransaction::new(
  inputs,
  &payments,
  None,
  Some(vec![0u8; 80]), // OP_RETURN output added at line 195
  fee_per_vbyte,
).unwrap();
// needed_fee = fee_per_vbyte * vbytes(tx WITHOUT the OP_RETURN output)
// tx.fee() == needed_fee, but actual tx.weight() includes the ~80-byte
// OP_RETURN output, so effective feerate < fee_per_vbyte.
// With fee_per_vbyte == 1 sat/vB, the ~90-byte data output leaves the
// transaction under the 1 sat/vB relay minimum and it is not propagated.
```
The weight-estimation transaction at lines 68–99 contains only `payments` outputs, while the real `tx.output` (lines 246–251) contains `tx_outs` including the OP_RETURN — a ~90+ vbyte discrepancy per `TooMuchData`'s 80-byte bound.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
```rust
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-206)
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
```
