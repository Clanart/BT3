### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/fee accounting, so the recorded `needed_fee` and standardness check do not reflect the transaction actually signed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Beanstalk report — where `s.recapitalized` was left at its pre-migration value even though the migration produced less value, leaving global accounting inconsistent with reality — `SignableTransaction::new` adds an OP_RETURN output to the transaction *after* computing weight/vbytes/fee, and never recomputes. The stored `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check reflect a transaction that does not contain the data output, while the transaction that is actually signed and broadcast does.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments`, then appends an OP_RETURN `TxOut` when `data` is supplied [1](#0-0) . However, both calls to `Self::calculate_weight_vbytes` are passed `payments` — not `tx_outs` — so the data output is excluded from the weight, vbytes, and fee computation entirely [2](#0-1) . Inside `calculate_weight_vbytes`, the mock `Transaction` is built only from `payments` plus an optional change output; there is no parameter for data [3](#0-2) .

Consequences:
- `needed_fee` (and the `TooLowFee` minimum-relay check at line 211) is computed against a vbytes figure that is too small by ~9 + `data.len()` bytes (up to ~89 vbytes).
- If change exists, `fee_with_change` is likewise understated [4](#0-3) .
- The `weight > MAX_STANDARD_TX_WEIGHT` standardness check can pass for a transaction that is actually non-standard [5](#0-4) .

The signatures remain valid because the sighash commits to the real transaction via `Prevouts::All` and `SighashCache::new(&self.tx.tx)` [6](#0-5) . The mismatch is purely in the accounting layer — exactly the Beanstalk pattern where a stored value (`s.recapitalized` / `needed_fee`) fails to be updated after an operation changes the real underlying quantity.

### Impact Explanation
The signed transaction pays an absolute fee equal to the understated `needed_fee`, so its *effective* feerate is lower than the `fee_per_vbyte` the caller requested — by up to roughly 89 vbytes worth of uncounted output weight. Where the calculated fee only marginally passed the `DEFAULT_MIN_RELAY_TX_FEE` floor, the real feerate can fall below the relay minimum, producing a validly-signed but unbroadcastable/stuck transaction that locks the multisig's inputs until a replacement is coordinated. Additionally, a transaction near the standard weight limit can be constructed and signed yet be rejected as non-standard by nodes. Callers relying on `needed_fee()` for accounting/fee reporting are told a number that does not match the transaction's true size.

### Likelihood Explanation
Any caller supplying `data` (an OP_RETURN payload, e.g., for embedded metadata/memo usage) triggers it deterministically — no adversary needed. An unprivileged party cannot directly invoke this, but it is reachable through the normal public path of constructing a `SignableTransaction` with untrusted `payments`/`data` bytes. The discrepancy scales with `data.len()` up to the 80-byte cap.

### Recommendation
Include the OP_RETURN output in the weight calculation: either push the data `TxOut` into the mock transaction inside `calculate_weight_vbytes` (add a `data: Option<&[u8]>` or generic `extra_outputs` parameter), or build `tx_outs` first and pass it in place of `payments`. Recompute `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check against the complete output set, mirroring the Beanstalk fix of re-deriving `s.recapitalized` from the true post-operation value.

### Proof of Concept
```rust
// networks/bitcoin: with inputs worth 10_000 sats, one payment of 5_000,
// no change, and data = vec![0; 80]:
let tx = SignableTransaction::new(inputs, &payments, None, Some(vec![0; 80]), fee_rate).unwrap();
// tx.weight() includes the ~89-byte OP_RETURN output;
// tx.needed_fee() == fee_rate * vbytes_computed_without_the_data_output.
// Therefore tx.fee() / tx.transaction().vsize() < fee_rate,
// and if vbytes_without_data just cleared DEFAULT_MIN_RELAY_TX_FEE,
// the actual feerate is below the relay minimum.
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

**File:** networks/bitcoin/src/wallet/send.rs (L188-202)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-234)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

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
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
```
