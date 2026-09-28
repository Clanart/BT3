### Title
Untrusted `ReceivedOutput` deserialization accepts an unverified `OutPoint`-to-`TxOut` binding - ([File: `networks/bitcoin/src/wallet/mod.rs`])

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, a `TxOut`, and an `OutPoint`, without proving that the referenced on-chain output exists or contains that `TxOut`. [1](#0-0) [2](#0-1) [3](#0-2)  `SignableTransaction` then uses the deserialized `OutPoint` as the spend target and the deserialized `TxOut` as the claimed prevout value. [4](#0-3) [5](#0-4) 

### Finding Description
`OutPoint` is only a reference to a prior transaction output, but `ReceivedOutput` stores a separately supplied `TxOut` alongside it. [6](#0-5)  The deserializer performs no chain lookup, Merkle proof validation, or equality check between the referenced output and the embedded output, so bytes can represent a nonexistent or mismatched prevout. [7](#0-6) 

When spending, `multisig` checks only that the embedded prevout’s `script_pubkey` matches the wallet key after applying the embedded offset; it does not establish that `input.outpoint` actually resolves to that script or amount. [8](#0-7)  The transaction-signing path then commits to all supplied prevouts with `Prevouts::All`. [9](#0-8) 

### Impact Explanation
An application that imports, caches, or receives serialized `ReceivedOutput` values from an untrusted source can report fake input value through `value()` and construct a spend request for funds that are not spendable. [10](#0-9)  The resulting transaction is signed over attacker-chosen prevout data and cannot be valid on Bitcoin if the referenced outpoint is absent or has different contents. [9](#0-8)  This is a medium-severity integrity issue because it corrupts wallet accounting and can induce signing of an invalid spend, but it does not let the attacker spend an unrelated output whose actual `script_pubkey` is not controlled by the wallet. [8](#0-7) 

### Likelihood Explanation
The trigger is reachable whenever untrusted serialized bytes are passed to `ReceivedOutput::read`; the format provides only raw fields and no authentication or consensus binding. [7](#0-6)  Exploitation requires a caller to treat deserialization as proof that the returned `ReceivedOutput` was discovered by `Scanner` or otherwise corresponds to an actual chain output. [11](#0-10) 

### Recommendation
Treat `ReceivedOutput` deserialization as data input rather than proof of ownership or existence, and document that invariant explicitly. [7](#0-6)  Before constructing a `SignableTransaction`, resolve each `outpoint` through a trusted node and require the returned consensus `TxOut` to equal `input.output`. [4](#0-3)  Prefer a separate untrusted wire representation that stores only `offset` and `outpoint`, then fetches the `TxOut` from the chain, or attach authenticated scanner provenance to trusted `ReceivedOutput` values. [11](#0-10) 

### Proof of Concept
For any registered `offset` whose derived wallet `script_pubkey` is known, forge this byte sequence:

```rust
// canonical 32-byte scalar
serialized.extend(offset.to_bytes());

// claimed prevout contents
serialized.extend(bitcoin::consensus::serialize(&TxOut {
  value: Amount::from_sat(10_000_000),
  script_pubkey: wallet_script_pubkey,
}));

// an outpoint which does not exist, is immature, already spent,
// or resolves to a different TxOut
serialized.extend(bitcoin::consensus::serialize(&OutPoint {
  txid: arbitrary_txid,
  vout: 0,
}));
```

`ReceivedOutput::read` accepts these bytes because it only decodes the three independently supplied fields. [7](#0-6)  `SignableTransaction::new` accepts the forged input as a 10,000,000-sat input and places the arbitrary `OutPoint` in the transaction. [4](#0-3)  `multisig` accepts the fake input if the embedded `script_pubkey` matches the offset-derived wallet key, after which signing commits to the forged prevout through `Prevouts::All`. [8](#0-7) [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
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

**File:** networks/bitcoin/src/wallet/send.rs (L253-255)
```rust
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-281)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
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
