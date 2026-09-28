### Title
OP_RETURN data is excluded from Bitcoin transaction fee estimation - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Medium. `SignableTransaction::new` appends the caller-controlled `data` as an OP_RETURN output, but both fee-estimation paths measure only `payments` and optional change, omitting that output. [1](#0-0) [2](#0-1) 

### Finding Description
`calculate_weight_vbytes` constructs its estimate solely from the supplied payment outputs plus optional change. [3](#0-2)  Before this estimate is made, `new` has already appended an OP_RETURN output containing up to 80 attacker-controlled bytes. [4](#0-3)  The call at line 204 nevertheless passes `payments`, not the actual `tx_outs`, and the change-aware call repeats the same omission. [5](#0-4)  Consequently, `needed_fee` is computed for a transaction smaller than the transaction subsequently signed. [6](#0-5) 

### Impact Explanation
An unprivileged party who can cause an OP_RETURN-bearing transaction to be signed can force Serai to produce a transaction whose actual fee rate is lower than the requested `fee_per_vbyte`. [7](#0-6)  With an 80-byte payload, the missing output adds roughly 91 virtual bytes, so the signed transaction can fall below the intended fee rate or the relay minimum checked against the underestimated size. [8](#0-7)  This can leave a signed withdrawal or relay transaction stuck or rejected despite the threshold signing path completing successfully. [9](#0-8) 

### Likelihood Explanation
The condition is deterministic whenever `data` is supplied; the only validation is the 80-byte length bound. [4](#0-3)  The defect occurs with and without change because both calls to `calculate_weight_vbytes` use `payments` rather than the complete output list. [5](#0-4) 

### Recommendation
Calculate weight from the actual `tx_outs` after adding every payment, OP_RETURN, and potential change output. [10](#0-9)  At minimum, change `calculate_weight_vbytes` to accept a representative `&[TxOut]` or explicitly include the OP_RETURN output in both the no-change and change estimates. [11](#0-10) 

### Proof of Concept
```rust
// Inside networks/bitcoin/src/wallet/send.rs tests, where ReceivedOutput
// can be constructed with a controlled value/outpoint.
let input = received_output_with_value(payment + expected_fee);
let data = vec![0; 80];

let tx = SignableTransaction::new(
  vec![input],
  &[(payment_script.clone(), payment)],
  None,
  Some(data),
  fee_per_vbyte,
).unwrap();

// The OP_RETURN output exists in the final transaction.
assert_eq!(tx.transaction().output.len(), 2);

// needed_fee was calculated without that output, while the actual
// transaction is larger. Therefore it does not reach the requested rate.
assert!(
  tx.needed_fee() <
    fee_per_vbyte * u64::try_from(tx.transaction().vsize()).unwrap()
);
```
This follows because `new` adds the OP_RETURN before estimating only `payments`. [1](#0-0)

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

**File:** networks/bitcoin/src/wallet/send.rs (L171-233)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
```
