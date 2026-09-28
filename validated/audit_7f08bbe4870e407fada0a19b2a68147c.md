### Title
Untrusted `ReceivedOutput` bytes can claim arbitrary, nonexistent UTXOs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary

`ReceivedOutput::read` deserializes an offset, claimed `TxOut`, and claimed `OutPoint` as three independent attacker-controlled fields without proving that the `TxOut` actually exists at that `OutPoint` or was produced by `Scanner`. [1](#0-0)  Legitimate objects are produced only when a transaction output’s `script_pubkey` maps to a registered offset, at which point the real `TxOut` and transaction-derived `OutPoint` are paired together. [2](#0-1) 

### Finding Description

The bug class is an object-identifier substitution: the attacker changes the identifier (`outpoint`), value, or script associated with an otherwise valid output record. `ReceivedOutput::read` accepts that modified record without re-establishing its ledger provenance. [3](#0-2) 

The parser does authenticate the offset against `output.script_pubkey` internally. Although `SignableTransaction::multisig` later checks that the wallet key plus the supplied offset controls the claimed `script_pubkey`, this check only validates the embedded `TxOut`; it does not prove that this `TxOut` resides at the embedded `OutPoint`. [4](#0-3) 

### Impact Explanation

If an attacker-controlled serialized `ReceivedOutput` reaches an import, recovery, forwarding, or RPC path that treats it as a discovered deposit, the attacker can report funds that are not spendable: for example, a large wallet-controlled `TxOut` paired with an unrelated or nonexistent `OutPoint`, or a real `OutPoint` whose amount was altered. [3](#0-2) 

This can cause incorrect balance accounting and rejected transactions. Spending uses `Prevouts::All`, so the claimed amounts and scripts are committed into every input’s Taproot sighash; if the real prevout differs, the produced signatures do not authorize spending it. [5](#0-4) 

### Likelihood Explanation

Exploitation requires an attacker-controlled byte stream to be passed to `ReceivedOutput::read` and for the consumer to trust it as scanner output. The format has no authentication or chain-binding check, so modifying the fields is straightforward; the limiting factor is whether such serialized records cross a trust boundary. [6](#0-5) 

### Recommendation

Do not treat deserialized `ReceivedOutput`s as evidence of funds. Before crediting or spending them, resolve `outpoint` against a confirmed chain view and compare the returned `TxOut` byte-for-byte with the embedded `output`. Alternatively, persist enough containing-transaction evidence to reconstruct the `txid` and `vout`, or authenticate scanner-derived records so externally supplied substitutions are rejected. [2](#0-1) 

### Proof of Concept

Conceptually, serialize a wallet-controlled `TxOut`, but pair it with a nonexistent `OutPoint`:

```rust
let mut bytes = offset.to_bytes().to_vec();
bytes.extend(serialize(&TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: wallet_script.clone(),
}));
bytes.extend(serialize(&OutPoint {
  txid: arbitrary_txid,
  vout: 0,
}));

let forged = ReceivedOutput::read(&mut bytes.as_slice())?;
```

`forged.value()` reports `1_000_000`, even though `arbitrary_txid:0` need not contain that output. A subsequent `SignableTransaction` can pass the key/script check when `wallet_script` belongs to the key and offset, but its signatures commit to the forged prevout amount and fail consensus validation against the actual UTXO. [7](#0-6) [8](#0-7) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L99-118)
```rust
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-149)
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
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
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
