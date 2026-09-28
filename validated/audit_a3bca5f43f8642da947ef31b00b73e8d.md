### Title
Duplicate `ReceivedOutput`s are counted and signed as independent inputs, producing an unintended Bitcoin transaction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` accepts an arbitrary `Vec<ReceivedOutput>` and never rejects two entries with the same `OutPoint`, so one UTXO can be counted multiple times in the funding total and inserted repeatedly into the transaction. [1](#0-0) 

### Finding Description
`ReceivedOutput::read` accepts externally supplied bytes containing an offset, output, and outpoint without requiring uniqueness across a transaction input list. [2](#0-1)  `SignableTransaction::new` then sums every supplied output value and converts every supplied entry into a `TxIn`, without maintaining a set of already-used outpoints. [3](#0-2)  Consequently, duplicating one serialized `ReceivedOutput` causes its value to be counted twice and its `previous_output` to appear twice in the transaction. [4](#0-3) 

### Impact Explanation
A caller can obtain signatures for a transaction whose apparent funding and outputs were authorized using the same UTXO more than once. [3](#0-2)  `multisig` creates a signing machine for every input, and signing commits each input index to the full prevout list. [5](#0-4) [6](#0-5)  `complete` writes a witness for each repeated input and returns the fully signed transaction. [7](#0-6)  This is concrete signing of an unintended transaction: the threshold group can authorize a spend whose payment amount or change was calculated from duplicated collateral rather than distinct owned outputs. [8](#0-7) 

### Likelihood Explanation
The trigger only requires supplying the same valid `ReceivedOutput` bytes twice to the public `ReceivedOutput::read` and `SignableTransaction::new` paths. [2](#0-1) [9](#0-8)  An unprivileged party that can submit transaction-construction data can therefore duplicate an output it caused the wallet to receive, without needing a validator key, malformed curve point, or colluding participant. [3](#0-2) 

### Recommendation
Reject duplicate `ReceivedOutput::outpoint` values before calculating `input_sat` or constructing `tx_ins`, for example by inserting every `OutPoint` into a `HashSet` and returning a new `TransactionError::DuplicateInput` on insertion failure. [3](#0-2) 

### Proof of Concept
```rust
// `received` is any valid output payable to the wallet.
let encoded = received.serialize();
let duplicate = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

let doubled = received.value() * 2;
let payment = doubled - sufficient_fee;

// This succeeds even though both entries spend identical `previous_output`s.
let tx = SignableTransaction::new(
  vec![received.clone(), duplicate],
  &[(destination_script, payment)],
  None,
  None,
  fee_per_vbyte,
).unwrap();

// `input_sat` was counted twice, and both transaction inputs reference
// `received.outpoint`.
assert_eq!(tx.transaction().input[0].previous_output, *received.outpoint());
assert_eq!(tx.transaction().input[1].previous_output, *received.outpoint());
```

The duplicate is then signed once per input because `multisig` creates one `AlgorithmMachine` for each entry and `complete` fills every corresponding witness. [5](#0-4) [7](#0-6)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-231)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-427)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
```

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
