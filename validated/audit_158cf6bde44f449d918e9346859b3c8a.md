### Title
OP_RETURN data output excluded from transaction weight/fee accounting - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/Kirstentat/serai--020/blob/master/networks/bitcoin/src/wallet/send.rs))

### Summary
The upstream bug is a space-accounting error: `line6_midi_transmit` failed to include the chunk buffer's size when calculating the space left before copying data, producing an overflow. In `bitcoin-serai`, `SignableTransaction::new` commits the same class of error: it appends the OP_RETURN `data` output to `tx_outs` before computing the transaction weight, yet `calculate_weight_vbytes` is called with `payments` — which does not contain the OP_RETURN output — so the "available space" (weight/vbytes) of the transaction being built is undercounted by exactly the size of that extra output. [1](#0-0) 

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments`, then pushes an additional `TxOut` carrying the OP_RETURN `data` payload. [2](#0-1)  It then estimates the transaction's weight and virtual size via `calculate_weight_vbytes(tx_ins.len(), payments, None)` — a function that reconstructs a template transaction whose `output` list is built *only* from `payments` and an optional `change`. [3](#0-2) [4](#0-3)  The OP_RETURN output already present in `tx_outs` is never fed into the estimator. The same omission occurs in the change-handling path, where `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` is used to derive `fee_with_change` and the change `value`. [5](#0-4) 

Concretely:
- `needed_fee = fee_per_vbyte * vbytes` and the minimum-relay-fee floor both use a `vbytes` that omits the OP_RETURN output (~12 + data-length base bytes, up to ~92 bytes, plus a small witness-negligible amount). [6](#0-5) 
- The change amount `input_sat - payment_sat - fee_with_change` is computed against an undercharged fee, so the change output is slightly larger than the intended fee rate dictates — a minor over-credit to change rather than the fee. [7](#0-6) 
- The standardness guard `weight > MAX_STANDARD_TX_WEIGHT` is evaluated against the underestimated `weight`, so a transaction whose true weight exceeds the 400,000 WU standardness limit can pass validation, be fully FROST-signed, and then be rejected by every relay node — the funds committed as inputs are locked in an output set that can only be recovered by re-signing a different transaction. [8](#0-7) 

This is exactly the report's pattern: a fixed-size chunk (the extra output) is written to the buffer while the space/size calculation fails to account for it.

### Impact Explanation
An unprivileged user who causes Serai to sign a Bitcoin transaction with an OP_RETURN `data` payload (the `data` field originates from user-supplied transaction data/instructions routed to `SignableTransaction::new`) obtains a threshold-signed transaction that:
- pays a lower absolute fee than the requested `fee_per_vbyte` rate and potentially below the relay minimum actually required for its true vsize, and
- can exceed `MAX_STANDARD_TX_WEIGHT` while passing the library's own size check, producing a broadcasted, validly-signed but non-standard transaction.

The multisig will have consumed nonces and produced a valid signature over a transaction the Bitcoin network will not relay or mine, leaving the associated inputs effectively unspendable until a new plan is constructed. This maps to the accepted impact of "funds reported received / spent via a transaction that is not usable" and an incorrect size-accounting formula reachable from public input data. [9](#0-8) 

### Likelihood Explanation
Any Bitcoin payout batch that includes a `data` payload triggers the undercount automatically — it is deterministic, not dependent on adversary timing. Exploiting the weight-limit bypass additionally requires the batch to sit within ~100 vbytes of the 400,000 WU standardness boundary (hundreds of inputs/outputs), which is plausible for large consolidation batches but not the common case; the systematic under-fee, by contrast, occurs on every transaction carrying `data`. Severity is bounded because the signature is still over the intended transaction and the failure mode is fund lockup / insufficient fee rather than forgery.

### Recommendation
Pass the actual `tx_outs` (including the OP_RETURN output) into `calculate_weight_vbytes` instead of `payments`, or add the OP_RETURN `TxOut` to the template before computing `weight`/`vbytes` — i.e., account for every chunk written to the transaction when determining its size. The same corrected estimator must be used for the `fee_with_change` calculation and the `MAX_STANDARD_TX_WEIGHT` check. Alternatively, add a post-construction assertion `self.tx.weight() == Weight::from_wu(weight)` so any future mismatch between estimated and actual size fails loudly instead of silently producing a non-relayable transaction. [10](#0-9) 

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs path, conceptually:
// Construct a SignableTransaction carrying a max-size OP_RETURN payload.
let data = vec![0xaa; 80];
let stx = SignableTransaction::new(
    inputs,                                     // ReceivedOutputs owned by the multisig key
    &payments,                                  // normal payments
    None,                                       // no change
    Some(data.clone()),                         // OP_RETURN output appended to tx_outs
    fee_per_vbyte,
).unwrap();

// Bug: `vbytes` was computed from `payments` only (line 204), omitting the
// OP_RETURN TxOut pushed at lines 194-202.
let actual_weight = stx.transaction().weight().to_wu();
let (_, estimated_vbytes) = (
    actual_weight,
    bitcoin::policy::get_virtual_tx_size(actual_weight as i64, 0) as u64,
);

// The OP_RETURN output adds ~(8 value + 1 len + 2 opcodes + 80 data) ≈ 91 base
// bytes ≈ 364 WU ≈ 91 vbytes that was never accounted for:
assert!(stx.fee() < fee_per_vbyte * estimated_vbytes); // underpaid fee
assert_eq!(
    stx.transaction().output.iter().filter(|o| o.script_pubkey.is_op_return()).count(),
    1
);

// Boundary case: choose inputs/payments so estimated `weight` is just below
// MAX_STANDARD_TX_WEIGHT; the real tx exceeds it, passes the check at line 241,
// gets FROST-signed via TransactionSignMachine::sign, and is then rejected by
// relay policy — inputs locked despite a valid signature.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-99)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
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

**File:** networks/bitcoin/src/wallet/send.rs (L188-213)
```rust
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

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

**File:** networks/bitcoin/src/wallet/send.rs (L225-234)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-398)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
  }
```
