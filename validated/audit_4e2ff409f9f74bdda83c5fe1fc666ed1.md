### Title
Unauthenticated `ReceivedOutput` deserialization bypasses scanner binding and admits non-spendable outputs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_transaction` creates `ReceivedOutput` only when an observed transaction output's `script_pubkey` is registered for the wallet key and records the matching outpoint. `ReceivedOutput::read` provides an alternate constructor that accepts independently supplied `offset`, `TxOut`, and `OutPoint` fields without validating that the output exists or that the outpoint refers to the supplied `TxOut`, allowing forged spendable-output records to reach transaction signing. [1](#0-0) [2](#0-1) 

### Finding Description
The scanner enforces the ownership boundary by looking up `output.script_pubkey` in its registered script map and deriving `outpoint` from the containing transaction. [1](#0-0) 

`ReceivedOutput::read` bypasses that boundary: it deserializes an arbitrary scalar offset, then independently decodes a `TxOut` and `OutPoint`, and returns them as a `ReceivedOutput` without checking whether the outpoint actually resolves to that output or whether the output was ever observed in a transaction. [2](#0-1) 

`SignableTransaction::new` trusts the supplied `ReceivedOutput` values as both the claimed previous outputs and the source of the input outpoints. [3](#0-2) [4](#0-3) 

The only later consistency check in `SignableTransaction::multisig` verifies that the deserialized `TxOut` script matches the key plus the supplied offset; it does not and cannot verify that the separately deserialized `OutPoint` refers to that `TxOut`. [5](#0-4) 

As a result, a party supplying serialized `ReceivedOutput` bytes can fabricate an apparently spendable input by pairing a real-looking output script and value with an outpoint for an unrelated or nonexistent UTXO. [2](#0-1) [5](#0-4) 

### Impact Explanation
An application that accepts serialized outputs from an untrusted source can report funds as received even though the referenced UTXO is absent or does not contain the claimed output. [2](#0-1) 

If the forged `TxOut` uses the correct script for `group_key + offset * G`, `multisig` accepts the forged record and constructs one signing machine per claimed input. [6](#0-5) 

`TransactionSignMachine::sign` then produces threshold signature shares committing to a sighash over the attacker-selected previous-output list and transaction. [7](#0-6) 

The resulting signed transaction will be rejected by Bitcoin when its referenced outpoint does not contain the claimed output, but the false received-output record has already crossed the scanner's intended boundary and may incorrectly affect balance, accounting, or signing decisions. [2](#0-1) [8](#0-7) 

### Likelihood Explanation
The attack requires only control over bytes passed to `ReceivedOutput::read`, which is one of the public-input paths considered reachable for this code. [2](#0-1) 

No private key material or cryptographic forgery is needed because the deserializer accepts all three fields independently, and `multisig` checks only the key-to-script relationship rather than the outpoint-to-output relationship. [5](#0-4) 

The impact is conditional on an integrator treating serialized `ReceivedOutput` values as untrusted scan results rather than trusted local state. [9](#0-8) 

### Recommendation
Do not expose `ReceivedOutput::read` as an untrusted-input constructor, or rename/document it as a trusted serialization routine. [9](#0-8) 

For untrusted inputs, require a validation API that checks `p2tr_script_buf(key + offset * G) == output.script_pubkey` and authenticates the `OutPoint -> TxOut` mapping against a confirmed Bitcoin transaction or trusted UTXO source before constructing `ReceivedOutput`. [10](#0-9) [1](#0-0) 

Prefer storing scanner results keyed by outpoint, or serializing the containing transaction/index information needed to prove the relationship, so deserialization cannot pair an arbitrary `TxOut` with an unrelated `OutPoint`. [11](#0-10) 

### Proof of Concept
A caller can forge bytes containing a zero offset, a `TxOut` with the wallet's expected script and a large value, and an unrelated outpoint.

```rust
// Serialize:
//   Secp256k1 scalar offset = 0
//   TxOut { value: claimed_amount, script_pubkey: wallet_script }
//   OutPoint { txid: attacker_selected_txid, vout: attacker_selected_vout }
let mut bytes = Vec::new();
bytes.extend(Scalar::ZERO.to_bytes());
bytes.extend(bitcoin::consensus::encode::serialize(&TxOut {
    value: Amount::from_sat(claimed_amount),
    script_pubkey: wallet_script,
}));
bytes.extend(bitcoin::consensus::encode::serialize(&OutPoint {
    txid: attacker_selected_txid,
    vout: attacker_selected_vout,
}));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
```

This succeeds because `read` returns the decoded fields without consulting the scanner or checking the outpoint against a transaction. [2](#0-1) 

If `wallet_script` is the P2TR script for the threshold key, `SignableTransaction::multisig` accepts the forged input and `sign` signs a transaction referencing `attacker_selected_txid`, even when that outpoint does not contain the claimed output. [5](#0-4) [12](#0-11)

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-191)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-210)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-184)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
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
