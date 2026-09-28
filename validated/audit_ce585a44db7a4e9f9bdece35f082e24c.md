### Title
`SignableTransaction::new` computes the transaction fee without accounting for the OP_RETURN data output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The bug class in the external report is a value calculated by a formula that omits a required term (BPT price = `poolRate * min(tokenPrices)` ignores that the rate already prices the whole pool), producing a computed value wildly divergent from the true one. `SignableTransaction::new` has the same class: it measures transaction weight/vbytes over the `payments` outputs only, even though a caller-supplied `data` argument adds an OP_RETURN output of up to ~80 bytes to the final transaction. The declared `needed_fee` and `fee_per_vbyte`-derived accounting therefore systematically understates the real transaction size.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` before the weight is computed, but the weight helper is called with `payments` — which does not include the data output:

- The data output is pushed at `send.rs:193-202`.
- Weight/vbytes are computed with `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs:204`, where `calculate_weight_vbytes` builds the template transaction `output` strictly from `payments` (`send.rs:85-93`) plus an optional change output (`send.rs:95-99`).

So a `data` payload of up to 80 bytes adds a serialized output (1 byte count + 8 byte value + scriptPubKey length + up to ~83 byte script) that is never counted. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) is then short by roughly `fee_per_vbyte * ~90` vbytes per transaction. The same omission applies to the change path, since `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at `send.rs:225-226` also only sees `payments`.

Additionally, the minimum-relay-fee check at `send.rs:211` uses the same understated `vbytes`, so a transaction can pass `TooLowFee` validation while its actual on-chain fee rate (real fee / real vsize) is below `DEFAULT_MIN_RELAY_TX_FEE`, or more generally below the `fee_per_vbyte` the caller requested.

### Impact Explanation
`needed_fee()` is documented as "the fee necessary for this transaction to achieve the fee rate specified at construction", but it underestimates the fee whenever `data` is supplied. When a change output exists, the change amount is computed as `input_sat - payment_sat - fee_with_change`, so the excess is routed to change and the transaction pays less than the intended fee rate; when there is no change, the leftover still becomes fee, but the *claimed* `needed_fee`/rate accounting is wrong. In the worst case the produced transaction's true fee rate is under min-relay and it will not propagate or confirm, leaving the spend unbroadcastable — the "computed value diverges from the real value with funds-affecting consequence" consequence of the reported class.

### Likelihood Explanation
Any invocation of `SignableTransaction::new` with a non-`None` `data` argument triggers the miscalculation deterministically; the undercount is proportional to the data length and the requested fee rate. No attacker sophistication is needed — only a caller embedding data (the documented purpose of the `data` parameter).

### Recommendation
Include the OP_RETURN output in the weight estimation: build the `output` list inside `calculate_weight_vbytes` (or the call sites at `send.rs:204` and `send.rs:225-226`) from the already-constructed `tx_outs` (payments + data) rather than from `payments` alone, or push the data output into a combined outputs slice before calling `calculate_weight_vbytes`. Also recompute the `TooLowFee` check against the corrected vbytes so the min-relay check reflects the transaction actually being signed.

### Proof of Concept
- `data` output added to the real transaction: `networks/bitcoin/src/wallet/send.rs` lines 193-202.
- Weight computed only over `payments` (+optional change): `send.rs` lines 62-99 (the `output` vec is built solely from `payments`), called at lines 204 and 225-226.
- Understated fee and min-relay check: `send.rs` lines 206-213 and 227-233.

Concretely: `SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0; 80]), fee_per_vbyte)` produces a `SignableTransaction` whose `tx.output` includes an ~90-byte OP_RETURN output, while `needed_fee` equals `fee_per_vbyte` times the vsize of a transaction *without* that output. The signed transaction's effective fee rate is therefore below `fee_per_vbyte`, and if `fee_per_vbyte` is near the minimum relay rate the resulting transaction falls below `DEFAULT_MIN_RELAY_TX_FEE` despite passing the `TooLowFee` check. [1](#0-0) [2](#0-1) [3](#0-2)

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
