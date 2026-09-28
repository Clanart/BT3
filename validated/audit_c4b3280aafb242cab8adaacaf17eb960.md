### Title
`ReceivedOutput::read` trusts attacker-supplied outpoints and amounts, allowing unspendable funds to be reported and signed - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a scalar offset, `TxOut`, and `OutPoint` without proving that the outpoint exists, remains unspent, or actually contains the claimed value. [1](#0-0)  An unprivileged party can therefore submit bytes representing an arbitrary balance under a victim’s P2TR script, causing downstream accounting to report funds that are not spendable. [2](#0-1) 

### Finding Description
The original bug class is a cached balance diverging from the externally controlled real balance. The Bitcoin analog is `ReceivedOutput`, which is documented as “A spendable output” but stores a self-contained `TxOut` rather than an authenticated reference to chain state. [3](#0-2) 

`ReceivedOutput::read` accepts all three fields from untrusted bytes and performs only syntactic decoding. [1](#0-0)  Its `value` method then returns the attacker-controlled amount embedded in that `TxOut`, not the amount of the referenced UTXO. [4](#0-3) 

The discrepancy propagates into transaction construction: `SignableTransaction::new` calculates `input_sat` from the supplied `input.output.value` fields. [5](#0-4)  Those same deserialized `TxOut`s become the Taproot `Prevouts::All` commitment used for signing. [6](#0-5) 

`SignableTransaction::multisig` verifies only that each claimed `script_pubkey` matches the threshold key after applying the supplied offset; it does not retrieve or authenticate the UTXO amount behind `previous_output`. [7](#0-6)  Consequently, an attacker who knows the public threshold address can make the wallet treat a nonexistent, spent, or misvalued outpoint as spendable input. [8](#0-7) 

### Impact Explanation
A system that persists or accepts serialized `ReceivedOutput`s can credit arbitrary BTC value that cannot be spent. [9](#0-8)  Inflation of a cached `TxOut` can also make `SignableTransaction::new` pass its internal `NotEnoughFunds` check even though the real UTXO set does not contain sufficient value. [10](#0-9) 

If the fake `TxOut` uses the threshold key’s P2TR script, the `multisig` consistency check succeeds and FROST participants are driven to sign a sighash committing to the attacker-chosen prevout data. [11](#0-10) [12](#0-11)  The resulting transaction is not consensus-valid when the claimed amount or outpoint does not match the UTXO, so this does not directly authorize spending another output; the concrete impact is false receipt/accounting of unspendable funds and threshold-signing work on an invalid transaction. [13](#0-12) [14](#0-13) 

### Likelihood Explanation
The reachable precondition is an integration exposing `ReceivedOutput::read` to untrusted or externally modifiable bytes, which is explicitly a public-input boundary. [1](#0-0)  Scanner-created objects are not inherently vulnerable because `Scanner::scan_transaction` copies outputs and outpoints from an observed transaction. [15](#0-14)  The vulnerability exists because the deserializer gives attacker-controlled bytes the same “spendable output” representation as scanner-derived objects, without a type-level or cryptographic provenance distinction. [3](#0-2) [1](#0-0) 

### Recommendation
Do not treat deserialized `ReceivedOutput`s as authoritative spendable UTXOs. Revalidate each `(outpoint, TxOut)` pair against a trusted UTXO source before reporting its `value`, calling `SignableTransaction::new`, or invoking `multisig`. Prefer storing only the offset and outpoint in untrusted input, then fetching the canonical `TxOut` during verification. If serialized wallet state must be trusted, authenticate it and expose a distinct unverified representation so `read` cannot silently mint a “spendable” output.

### Proof of Concept
1. Obtain the public threshold group key and derive its P2TR script with `p2tr_script_buf`. [16](#0-15) 
2. Serialize:
   - `offset = Scalar::ZERO`,
   - a `TxOut` whose `script_pubkey` is that P2TR script but whose `value` is arbitrarily inflated,
   - an arbitrary or previously spent `OutPoint`. [17](#0-16) 
3. Feed those bytes to `ReceivedOutput::read`; decoding succeeds because it checks only serialization syntax. [1](#0-0) 
4. Call `value()` or pass the object to `SignableTransaction::new`; the fake amount is counted as input value. [4](#0-3) [18](#0-17) 
5. Calling `multisig` passes the script-key consistency check because the script was honestly derived from the public key, despite the outpoint/amount being false. [19](#0-18) 
6. Signing commits to the attacker-chosen prevout amount through `Prevouts::All`, but the completed transaction is invalid when broadcast because it does not match actual UTXO state. [12](#0-11) [14](#0-13)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L88-134)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}

impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }

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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-148)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
```rust
  /// Scan a transaction.
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-220)
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

**File:** networks/bitcoin/src/wallet/send.rs (L413-427)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
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
