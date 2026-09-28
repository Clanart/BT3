Let me look at how offsets are applied during signing.Let me check `ThresholdKeys::offset`/`scale` semantics and the FROST sign path for how offsets and the TapTweak interact.### Title
`SignableTransaction::new` omits the OP_RETURN `data` output from the weight/vbytes/fee calculation, producing underpriced and potentially non-relayable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a `data` payload is supplied, `SignableTransaction::new` appends an OP_RETURN output to `tx_outs`, but the size/weight used for fee calculation, the minimum-relay-fee check, the change-amount calculation, and the `MAX_STANDARD_TX_WEIGHT` check are all computed from `payments` only. The data output (up to 80 bytes of payload plus output/script overhead, ~90+ bytes) is completely unaccounted for. This mirrors the incident class — user-supplied inputs cause the platform to commit funds to a transaction that does not actually do what was priced — here, deposits/inputs are locked into a transaction whose real feerate is lower than the rate the caller paid for and which can fall below Bitcoin's minimum relay feerate, leaving funds unconfirmable and effectively unrecoverable through the normal path.

### Finding Description
In `SignableTransaction::new`:

- The OP_RETURN output is pushed onto `tx_outs` at lines 193–202.
- The weight/vbytes are then computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, which builds the template transaction **from `payments` only** (lines 85–94) — the OP_RETURN output is never included.
- `needed_fee = fee_per_vbyte * vbytes` (line 206) therefore under-prices the real transaction.
- The minimum-relay check at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` — both sides use the same underestimated `vbytes`, so the check reduces to `fee_per_vbyte >= min_rate` and does not detect that the *actual* feerate `fee_per_vbyte * v / (v + Δ)` may be below the relay minimum once the missing ~Δ≈90–95 vbytes of the OP_RETURN output are added.
- The change path (lines 224–235) uses `fee_with_change = fee_per_vbyte * vbytes_with_change`, again computed without the data output, so `change = input_sat - payment_sat - fee_with_change` leaves an actual fee equal to the underpriced `fee_with_change`.
- The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 likewise uses weight excluding the data output.

`calculate_weight_vbytes` only includes payment outputs and an optional change output; there is no path that accounts for `data`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
Any Bitcoin transaction built through this API with a non-empty `data` payload pays a lower effective feerate than `fee_per_vbyte` requested. For small transactions (few inputs/outputs, ~150–250 vbytes) and a near-minimum fee rate, adding ~90 unaccounted vbytes can drop the effective feerate by roughly a third, below `DEFAULT_MIN_RELAY_TX_FEE`. The `TooLowFee` guard cannot catch this because it is evaluated against the same underestimated size. The result is a transaction that is signed and broadcast but rejected or never relayed/confirmed, freezing the inputs (which may be user deposits scanned via `Scanner`/`get_outputs`) — funds committed on the expectation the transaction exists, while no valid spendable/confirmable transaction pays the promised rate. In the worst case the weight check is also evaded, producing a non-standard oversize transaction that cannot be relayed at all. This is loss-of-funds availability (stuck inputs) rather than theft, consistent with Medium severity.

### Likelihood Explanation
Triggering requires (a) a non-empty `data` argument and (b) a low `fee_per_vbyte` or a transaction small enough that the ~90-byte omission materially changes the feerate. `data` is allowed up to 80 bytes and is used by the processor to embed instructions (e.g., `Shorthand` payloads in OP_RETURN, as seen in `extract_serai_data` usage and the mint/burn flow). The bug is deterministic — it exists for every `data`-bearing transaction — but whether it renders a transaction unrelayable depends on the chosen fee rate; at worst it silently under-pays the requested rate. Medium likelihood.

### Recommendation
Include the OP_RETURN output in the size accounting: pass the fully-constructed `tx_outs` (or an explicit `data` output template) into `calculate_weight_vbytes` rather than `payments`, both for the no-change and with-change calculations, so that `needed_fee`, `fee_with_change`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check all cover every output actually serialized into the final `Transaction`.

### Proof of Concept
Conceptual, from `networks/bitcoin/src/wallet/send.rs`:

1. Call `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte = 1)` with `inputs` covering `payment_sat + needed_fee` exactly.
2. The returned transaction contains `payments.len() + 1` outputs (the OP_RETURN at lines 195–201), but `needed_fee` was computed from `calculate_weight_vbytes(tx_ins.len(), payments, change)` which never includes that output (lines 85–99, 204, 225–227).
3. `tx.fee() / tx.vsize()` (the real feerate) is strictly less than `fee_per_vbyte`; for a ~150-vbyte payment set the effective rate ≈ `1 * 150/244 ≈ 0.61` sat/vbyte — below `DEFAULT_MIN_RELAY_TX_FEE` (1000 sat/kvB = 1 sat/vB) — while the `TooLowFee` check at line 211 passed because it divided both sides by the same underestimated `vbytes`.
4. `rpc.send_raw_transaction`/network relay rejects the transaction; the consumed `ReceivedOutput`s remain unspent-by-this-tx yet the signing session already committed to them, and no correctly-priced transaction was ever formed — demonstrating the formula is incorrect for any `data != None`.

Caveat: I verified the accounting omission directly in `send.rs`, but could not fully trace every caller of `SignableTransaction::new` inside the processor within available iterations to confirm the maximum attacker-influenced `data` length; the code-level defect itself is unconditional.

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

**File:** networks/bitcoin/src/wallet/send.rs (L224-243)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
