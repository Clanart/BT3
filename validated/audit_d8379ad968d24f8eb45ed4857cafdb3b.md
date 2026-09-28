### Title
Duplicate `ReceivedOutput` inputs inflate available funds and produce an invalid transaction - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` does not reject duplicate `outpoint`s. An attacker who can provide the deserialized `ReceivedOutput` list can include the same UTXO multiple times, causing Serai to calculate spending capacity from duplicated value and construct a transaction that Bitcoin consensus rejects.

### Finding Description
`ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` without enforcing that the resulting object is unique within any later input set [1](#0-0) . `SignableTransaction::new` then sums every supplied input's value and independently serializes every supplied `outpoint` into a `TxIn` [2](#0-1) . No deduplication or comparison of `previous_output` occurs before the transaction and corresponding per-input FROST machines are created [3](#0-2) . Signing commits each input to all claimed prevouts, but it does not repair the duplicate-input semantic error [4](#0-3) .

### Impact Explanation
A transaction containing two inputs with the same `(txid, vout)` attempts to consume one UTXO twice and is invalid under Bitcoin transaction-validation rules. The constructed transaction therefore cannot be broadcast or confirmed even though Serai calculated its payments, change, and fee from the inflated duplicated balance [5](#0-4) . If an application accepts output descriptors or funding proposals from an untrusted party, this can turn attacker-controlled input framing into an authorization outcome that the local code treats as sufficiently funded but the network rejects.

### Likelihood Explanation
The attacker only needs control over serialized `ReceivedOutput` records or influence over the input vector and can repeat one otherwise-valid record. Exploitation requires the caller to pass that attacker-shaped list into `SignableTransaction::new`; it does not require a malicious validator, malformed curve encoding, leaked key, or unsafe code. The impact is a failed spend rather than direct signature forgery, so the practical severity is Medium.

### Recommendation
Reject duplicate outpoints during `SignableTransaction::new`. Insert each `input.outpoint` into a `HashSet<OutPoint>` before constructing `tx_ins`, and return a dedicated `TransactionError::DuplicateInput` on insertion failure. This check should occur before summing `input_sat` so duplicated value can never affect fee, payment, or change calculations [6](#0-5) .

### Proof of Concept
```rust
// `output` is one legitimately scanned or attacker-supplied ReceivedOutput.
let duplicate = output.clone();

// The same OutPoint is counted and serialized twice.
let tx = SignableTransaction::new(
  vec![output, duplicate],
  &[(destination_script, payment_amount)],
  None,
  None,
  fee_per_vbyte,
)?;

// The resulting transaction has two TxIn values with the same
// previous_output. Bitcoin nodes reject it as a double spend within
// one transaction, despite Serai treating the duplicated value as
// available funding.
assert_eq!(
  tx.transaction().input[0].previous_output,
  tx.transaction().input[1].previous_output,
);
```

The vulnerable behavior follows directly from cloning the `outpoint` into every `TxIn` while summing every supplied `output.value` [7](#0-6) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-235)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

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
```

**File:** networks/bitcoin/src/wallet/send.rs (L270-284)
```rust
  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
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
