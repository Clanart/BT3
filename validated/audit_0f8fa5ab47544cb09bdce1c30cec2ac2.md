[1](#0-0) ### Title
OP_RETURN data is omitted from transaction weight and fee accounting - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` appends caller-supplied data as an OP_RETURN output, but calculates transaction weight, fee requirement, minimum-relay eligibility, and the maximum-standard-weight check as though that output does not exist. [2](#0-1) 

### Finding Description
The constructor accepts up to 80 bytes of public `data` and adds it to `tx_outs` as a zero-value `ScriptBuf::new_op_return` output. [3](#0-2) [4](#0-3) 

However, the initial fee calculation passes only `payments` to `calculate_weight_vbytes`, omitting the already-created OP_RETURN output. [5](#0-4) 

The same mistake occurs when a change output is considered: `calculate_weight_vbytes` is again called with `payments`, not with the payment outputs plus the OP_RETURN output. [6](#0-5) 

`calculate_weight_vbytes` builds its estimate solely from the supplied `payments` and optional change script. [7](#0-6) 

The final transaction nevertheless contains `tx_outs`, including the omitted OP_RETURN output, so the signed transaction is larger than the transaction used for fee and weight validation. [8](#0-7) [9](#0-8) 

### Impact Explanation
`needed_fee` is computed from an underestimated virtual size, while the actual transaction pays only that underestimated fee when a change output absorbs the remaining input value. [10](#0-9) [11](#0-10) 

For an 80-byte payload, the OP_RETURN output adds approximately 92 serialized non-witness bytes—368 weight units or 92 vbytes—which are absent from `needed_fee`. [12](#0-11) 

At a requested rate of 1 sat/vbyte, the constructor’s minimum-relay check passes because it compares the estimated fee with the estimated size, but the final transaction is approximately 92 vbytes larger and pays less than the minimum relay rate. [13](#0-12) 

The threshold signature will still be produced for this malformed transaction because signing commits to the final `tx` containing the omitted output. [14](#0-13) 

The result is a successfully signed transaction whose payments can fail relay and never confirm, making the spent inputs unavailable through the intended transaction. [15](#0-14) 

### Likelihood Explanation
An unprivileged caller supplying transaction data can trigger this deterministically by providing a nonzero `data` value, especially the maximum 80-byte payload, and specifying a change output so the actual fee is fixed to the underestimated `fee_with_change`. [3](#0-2) [6](#0-5) 

No malformed encoding, malicious validator, invalid curve point, or protocol misuse is required; the data path is explicitly supported by the public constructor. [16](#0-15) 

The omission is limited to about 368 weight units, so the issue is a fee-estimation and standardness failure rather than an arbitrarily large underestimation. [4](#0-3) 

### Recommendation
Calculate weight and vbytes from the complete output set, including the OP_RETURN output, before computing `needed_fee`, checking the minimum relay fee, deciding whether change can be added, and enforcing `MAX_STANDARD_TX_WEIGHT`. [17](#0-16) 

A regression test should compare `needed_fee` against the actual `vsize()` of a witness-populated transaction for payloads of 0, 75, 76, and 80 bytes. [18](#0-17) 

### Proof of Concept
For one P2TR input, one P2TR payment, one P2TR change output, and `data = vec![0; 80]`, the added OP_RETURN output serializes to 92 bytes: 8 bytes of value, a 1-byte script length, and an 83-byte script (`OP_RETURN`, `PUSHDATA1`, length, and payload). [19](#0-18) 

The following test shape demonstrates the discrepancy:

```rust
let tx = SignableTransaction::new(
  vec![input],
  &[(payment_script, DUST)],
  Some(change_script),
  Some(vec![0; 80]),
  1, // sat/vbyte
).unwrap();

let mut actual = tx.transaction().clone();
for input in &mut actual.input {
  input.witness = Witness::from_slice(&[vec![0; 64]]);
}

assert_eq!(tx.fee(), tx.needed_fee());
assert_eq!(actual.vsize() as u64, tx.needed_fee() + 92);
assert!(tx.fee() < actual.vsize() as u64);
```

The constructor accepts the transaction because the minimum-fee check uses only the underestimated `vbytes`, while the completed transaction is 92 vbytes larger and has an actual fee rate below 1 sat/vbyte. [13](#0-12) [20](#0-19)

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

**File:** networks/bitcoin/src/wallet/send.rs (L137-140)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
```

**File:** networks/bitcoin/src/wallet/send.rs (L149-155)
```rust
  /// If data is specified, an OP_RETURN output will be added with it.
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
```

**File:** networks/bitcoin/src/wallet/send.rs (L171-251)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L355-397)
```rust
  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

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
```

**File:** networks/bitcoin/src/wallet/send.rs (L417-425)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }
```

**File:** networks/bitcoin/tests/wallet.rs (L172-190)
```rust
    // Data
    assert!(SignableTransaction::new(inputs.clone(), &[], None, Some(vec![]), FEE).is_ok());
    // No outputs
    assert_eq!(
      SignableTransaction::new(inputs.clone(), &[], None, None, FEE),
      Err(TransactionError::NoOutputs),
    );

    assert_eq!(
      SignableTransaction::new(inputs.clone(), &[(addr(), 1)], None, None, FEE),
      Err(TransactionError::DustPayment),
    );

    assert!(
      SignableTransaction::new(inputs.clone(), &payments, None, Some(vec![0; 80]), FEE).is_ok()
    );
    assert_eq!(
      SignableTransaction::new(inputs.clone(), &payments, None, Some(vec![0; 81]), FEE),
      Err(TransactionError::TooMuchData),
```
