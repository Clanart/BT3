### Title
Untrusted `ReceivedOutput` bytes can fake an unspendable Bitcoin input - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an attacker-controlled scalar offset, arbitrary consensus-encoded `TxOut`, and arbitrary `OutPoint` without proving that the output exists or belongs to the key implied by the offset. [1](#0-0)  `SignableTransaction::new` then trusts the claimed output value and outpoint as an input, while `multisig` only checks that `key + offset` produces the supplied script. [2](#0-1) [3](#0-2) 

### Finding Description
The scanner creates a `ReceivedOutput` only after matching an actual transaction output against a registered script, preserving the relationship between output, outpoint, and offset. [4](#0-3)  The deserializer bypasses that invariant: it reads all three fields independently and returns success without checking that the script equals a registered scanner script, that the offset is valid for a particular base key, or that the referenced outpoint exists. [1](#0-0)  Because P2TR output scripts are public, an attacker can encode a fictitious outpoint, an inflated amount, the victim multisig's P2TR script, and offset zero. [5](#0-4) 

### Impact Explanation
The fabricated record reports attacker-chosen funds through `ReceivedOutput::value` even though its `OutPoint` may reference no spendable chain output. [6](#0-5)  Transaction construction uses that fabricated value and outpoint as spendable input data. [2](#0-1)  If the supplied script is the multisig's normal P2TR script and the supplied offset is zero, `multisig` accepts the input and creates signing machines for a transaction spending a nonexistent output. [3](#0-2)  This causes funds to be reported received and selected for spending when they are not spendable.

### Likelihood Explanation
The attacker only needs to cause the victim to process serialized `ReceivedOutput` bytes; they do not need to create a valid funding transaction or know any private key. [1](#0-0)  The target script is public, and a zero offset satisfies the later script check for the untweaked scanner key. [5](#0-4)  The attack fails only if every caller obtains `ReceivedOutput` exclusively from `Scanner::scan_transaction` or otherwise authenticates the record before use. [4](#0-3) 

### Recommendation
Do not treat deserialized `ReceivedOutput` values as scanner-authenticated without an additional key/script binding check. [1](#0-0)  Add a checked constructor or context-aware read API that requires the expected base key and verifies `p2tr_script_buf(base + offset) == output.script_pubkey`, and reject records whose outpoints were not observed on-chain. [7](#0-6)  Persist and consume a scanner-authenticated wrapper rather than raw attacker-controlled `ReceivedOutput` bytes.

### Proof of Concept
```text
1. Let victim_script be p2tr_script_buf(victim_group_key), which is public.
2. Supply these bytes to ReceivedOutput::read:
   - offset: canonical Secp256k1 scalar 0
   - TxOut: consensus encoding of { value: chosen_large_amount,
                                  script_pubkey: victim_script }
   - OutPoint: consensus encoding of any nonexistent txid:vout
3. ReceivedOutput::read returns Ok despite the outpoint being absent.
4. Pass the resulting object to SignableTransaction::new.
5. Call multisig with the victim ThresholdKeys.
6. The check p2tr_script_buf(keys.offset(0).group_key()) ==
   prevouts[0].script_pubkey succeeds, so a signing machine is created
   for an unspendable input.
```
The decisive issue is that deserialization reconstructs the three fields without restoring the scanner-derived invariant between them. [1](#0-0)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-190)
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
