[1](#0-0) ### Title
Unauthenticated `ReceivedOutput` deserialization permits fabricated, unspendable deposits - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and `OutPoint` without proving that the output exists or that the serialized object was produced by `Scanner`. [1](#0-0) 

### Finding Description
`ReceivedOutput::read` canonicalizes the scalar offset but then directly deserializes attacker-controlled `TxOut` and `OutPoint` values, with no check binding the output to the scanner’s registered key offset or to an actual UTXO. [1](#0-0)  A deserialized output’s claimed amount is later treated as spendable input value, its claimed outpoint is placed directly into the transaction inputs, and its `TxOut` is trusted as the previous output. [2](#0-1) [3](#0-2)  The only key-related check before creating the signing machine is that the previous output’s script equals the P2TR script for the group key plus the supplied offset, which an attacker can satisfy for a fabricated outpoint. [4](#0-3) 

### Impact Explanation
An attacker who can supply serialized `ReceivedOutput` bytes can report an arbitrary amount under an arbitrary outpoint and receive signatures for a transaction attempting to spend that nonexistent output. [5](#0-4)  The resulting transaction is cryptographically well-formed but cannot be confirmed because its previous output does not exist, causing funds to be reported or accounted as received when they are not spendable. [3](#0-2) 

### Likelihood Explanation
Exploitation requires an application to deserialize `ReceivedOutput` objects from attacker-controlled storage or messages rather than retaining authenticated scanner-produced objects. [1](#0-0)  Because the serialized form contains no MAC, provenance marker, block commitment, or key-registration proof, no additional cryptographic assumption needs to be broken once that input path exists. [6](#0-5) 

### Recommendation
Do not feed untrusted bytes to `ReceivedOutput::read`, or authenticate serialized outputs with a key separated from the underlying wallet secret. [1](#0-0)  Before spending a decoded output, rescan or otherwise prove that the outpoint exists on-chain, that its `TxOut` exactly matches the decoded value and script, and that its script is the current scanner-derived `key + offset·G` P2TR output. [7](#0-6) 

### Proof of Concept
1. Let `K` be the target wallet’s public group key, choose a canonical `offset` such that `K + offset·G` is even-Y, and construct `script = p2tr_script_buf(K + offset·G).unwrap()`. [8](#0-7) 
2. Serialize `offset` followed by a fabricated `TxOut { value: large_amount, script_pubkey: script }` and a fabricated `OutPoint`, then pass those bytes to `ReceivedOutput::read`; deserialization succeeds despite the outpoint being absent from the UTXO set. [9](#0-8) 
3. Pass the fabricated `ReceivedOutput` to `SignableTransaction::new`; its amount contributes to `input_sat`, its fake outpoint becomes a transaction input, and its fabricated `TxOut` becomes a signing prevout. [2](#0-1) [3](#0-2) 
4. `multisig` accepts the input because the fabricated script was intentionally generated for the correct offset-adjusted group key. [4](#0-3) 
5. The FROST transaction machines then sign the input’s Taproot key-spend sighash and return a signed transaction, but Bitcoin consensus rejects it because the referenced prevout was fabricated. [5](#0-4) [10](#0-9)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L77-86)
```rust
/// Return the Taproot address payload for a public key.
///
/// If the key is odd, this will return None.
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-133)
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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
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
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
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
