### Title

Attacker-controlled OP_RETURN data is excluded from transaction fee and size accounting - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` adds an OP_RETURN output containing caller-controlled `data`, but calculates transaction weight and required fees using only `payments`. Consequently, every `data` byte is signed into the transaction without being charged against the requested fee rate.

### Finding Description

`SignableTransaction::new` accepts up to 80 bytes of `data` and appends it as a zero-value OP_RETURN output before calculating the fee. Both the no-change and change calculations call `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which constructs a sizing transaction only from `payments`, omitting the already-added OP_RETURN output. The final `SignableTransaction` nevertheless contains that output. [1](#0-0) [2](#0-1) 

The signing path then computes the BIP-341 signature hash over the actual transaction, including the unpriced OP_RETURN output, and all inputs under `Prevouts::All`. [3](#0-2) 

### Impact Explanation

An unprivileged party that can cause a wallet to create a transaction with chosen OP_RETURN data can cause the threshold group to sign a transaction whose fee rate is lower than requested and potentially below the relay minimum enforced by the constructor. The resulting transaction may be rejected or remain unconfirmed, leaving the selected inputs unavailable for spending until the signing attempt is abandoned or replaced. [4](#0-3) 

### Likelihood Explanation

The only precondition is that attacker-controlled transaction metadata reaches `SignableTransaction::new` through its public `data` parameter; no validator key, leaked secret, malformed curve point, or invalid signature is required. The discrepancy is deterministic whenever `data` is present because it is inserted before sizing but is absent from both sizing invocations. [5](#0-4) [6](#0-5) 

### Recommendation

Calculate weight from the complete output set, including the OP_RETURN output, for both the no-change and change paths. Alternatively, make `calculate_weight_vbytes` accept the final `tx_outs` list so every signed output is necessarily included in fee and size accounting. Add a regression test asserting that a transaction containing an 80-byte OP_RETURN pays at least `fee_per_vbyte * tx.vsize()`. [7](#0-6) 

### Proof of Concept

1. Call `SignableTransaction::new(inputs, payments, change, Some(vec![0xaa; 80]), fee_per_vbyte)`.
2. The function stores an OP_RETURN output in `tx_outs` before requesting sizing information.
3. Both calls to `calculate_weight_vbytes` pass `payments`, not `tx_outs`, so the 80-byte push and output overhead are absent from `vbytes` and `vbytes_with_change`.
4. `needed_fee` is therefore calculated for a smaller transaction than `tx`, while `fee()` reports the actual fee paid by the signed transaction.
5. At a boundary fee rate, the undersized `vbytes` value can pass the minimum-fee check even though the signed transaction’s actual virtual size requires a larger fee. [8](#0-7)

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

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L171-255)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
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
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }

    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
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
