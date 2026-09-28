### Title
Fee bound computed on a stripped transaction excludes the OP_RETURN output, so the signed transaction pays a lower effective fee rate than specified and may be non-relayable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` builds `tx_outs` including an optional `OP_RETURN` data output, but computes the transaction weight/vbytes — and therefore `needed_fee`, the minimum-relay check, and the change amount — from `payments` only, omitting the data output entirely. The bug class of the external report (a protective bound derived from a manipulable/incorrect quote rather than from the object actually executed) maps here: the fee bound is derived from a transaction that differs from the one actually constructed and signed.

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output is appended to `tx_outs` (lines 194-202), but the weight estimation calls use `payments`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

and again for the change case `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`. `calculate_weight_vbytes` builds the template transaction from the `payments` slice, so the `OP_RETURN` script (up to 80 bytes of data plus overhead, ≈90 vbytes) is never counted. Consequences:

- `needed_fee = fee_per_vbyte * vbytes` underprices the transaction; the real signed tx is larger, so the *effective* fee rate is below the caller-specified `fee_per_vbyte`.
- The min-relay check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` uses the same underestimated `vbytes`, so a transaction can pass this check while its true fee rate is below the relay minimum → `send_raw_transaction` rejects it.
- The change amount `input_sat - payment_sat - fee_with_change` absorbs the difference, so the discrepancy silently routes into change rather than fee, making detection harder.

### Impact Explanation
A signed Bitcoin transaction that underpays relative to the intended fee rate can fail broadcast (below min relay) or languish unconfirmed. For the multisig wallet this means funds committed in a `Plan` cannot move as intended — outputs the protocol believes are spendable/paid are effectively stalled until a corrected transaction is re-signed. This is a "funds not spendable as reported / bound weaker than assumed" analog: the slippage-style protection (`needed_fee`, min-relay check) is computed against a different (smaller) object than the one executed.

### Likelihood Explanation
The discrepancy triggers on every `SignableTransaction::new` call with `data: Some(_)` — no attacker action is strictly required, it is a systematic miscount. Reachability by an unprivileged party exists insofar as transaction `data`/`data.len()` derives from user-supplied instruction data flowing through the processor (bounded to 80 bytes at line 171, and to `MAX_DATA_LEN` upstream in `instruction_from_output`), letting a sender influence the size gap between the estimated and signed transaction. Severity: Medium — fund loss is indirect (stall/rejection rather than theft), bounded by the multisig's ability to re-sign.

### Recommendation
Pass the actual output list (including the `OP_RETURN` output) to `calculate_weight_vbytes`, i.e. compute `vbytes`/`weight` over `tx_outs` (plus the change output when present) rather than `payments`, so `needed_fee`, the min-relay check, and the change value all reflect the transaction actually signed.

### Proof of Concept
1. Call `SignableTransaction::new(inputs, payments, Some(change), Some(vec![0u8; 80]), fee_per_vbyte)` with `fee_per_vbyte` set exactly to the minimum relay rate.
2. The internal check passes because `vbytes` excludes the ~90-vbyte `OP_RETURN` output.
3. Sign via `tx.multisig(...)` and broadcast: `send_raw_transaction` rejects or the tx is evicted, since the true fee rate is `needed_fee / actual_vsize < min relay`. `tx.fee()` confirms the paid fee equals the underestimated `needed_fee` while `tx.vsize()` exceeds the estimated `vbytes`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Note: I could not fully trace whether the outgoing `data` payload is directly attacker-controlled end-to-end (the plan-construction path in `processor/src/plan.rs`/`bitcoin.rs` was not fully inspected); the systematic miscount is confirmed, and attacker influence over `data.len()` up to the 80-byte bound is plausible but not exhaustively verified.

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-213)
```rust
    // Add the OP_RETURN output
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

**File:** networks/bitcoin/src/wallet/send.rs (L223-235)
```rust
    // If there's a change address, check if there's change to give it
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

**File:** processor/src/multisigs/mod.rs (L55-64)
```rust
  let mut data = output.data();
  let max_data_len = usize::try_from(MAX_DATA_LEN).unwrap();
  if data.len() > max_data_len {
    error!(
      "data in output {} exceeded MAX_DATA_LEN ({MAX_DATA_LEN}): {}. skipping",
      hex::encode(output.id()),
      data.len(),
    );
    return (presumed_origin, None);
  }
```
