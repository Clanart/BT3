### Title
Deserialized `ReceivedOutput` permits forged, unspendable UTXOs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an offset, transaction output, and outpoint as three independent, unauthenticated fields. A caller can therefore deserialize a struct whose `output.script_pubkey` matches a registered scanner key while its `outpoint` and value refer to a nonexistent or unrelated UTXO. `SignableTransaction` subsequently trusts the forged outpoint/value, and `multisig` only checks that the offset-derived group key produces the supplied script pubkey.

### Finding Description
`ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` without validating that the output exists on-chain or that it was produced by `Scanner::scan_transaction` [1](#0-0) . Legitimate outputs are normally established by matching a transaction's `script_pubkey` and storing that transaction's actual `txid`/vout [2](#0-1) . Deserialization bypasses that provenance check.

During transaction construction, the supplied `outpoint` becomes the transaction input and the supplied `TxOut` becomes the committed prevout [3](#0-2) . `SignableTransaction::multisig` checks only whether `keys.offset(offset).group_key()` maps to `prevouts[i].script_pubkey`; it does not verify the outpoint or value [4](#0-3) . The resulting FROST machines then sign Taproot sighashes committing to those attacker-controlled prevouts [5](#0-4) .

### Impact Explanation
An attacker can provide bytes representing a valid-looking received output while replacing the real `OutPoint` or inflating `TxOut.value`. The wallet may report or consume funds that cannot be spent, and the threshold group can be induced to produce signatures for a transaction Bitcoin nodes will reject because the referenced prevout is absent, spent, or has different contents. This directly creates the “funds reported received that are not spendable” condition.

### Likelihood Explanation
The attack is reachable by any party able to supply serialized `ReceivedOutput` bytes or alter stored/untrusted wallet data. The attacker does not need threshold-key material: preserving a valid `offset` and `script_pubkey` while changing the outpoint/value is sufficient to pass the cryptographic ownership check. Serialization provides no MAC, commitment, chain proof, or binding to scanner state.

### Recommendation
Treat `ReceivedOutput` as an internal trusted type and avoid accepting it from untrusted sources. If deserialization is required, authenticate the complete serialized object or revalidate it against chain data before constructing `SignableTransaction`. At minimum, transaction construction should verify each claimed outpoint exists and that its on-chain `TxOut` exactly matches the supplied output.

### Proof of Concept
1. Obtain or construct a valid serialized `ReceivedOutput` for a scanner-controlled `script_pubkey`.
2. Replace its `outpoint` with a nonexistent txid/vout, or replace `output.value` with an inflated amount while retaining the same script.
3. Pass the modified bytes to `ReceivedOutput::read`; parsing succeeds because the fields are independent [1](#0-0) .
4. Pass the result to `SignableTransaction::new`; the forged outpoint/value are copied into the spend [3](#0-2) .
5. `multisig` accepts the input because it compares only the offset-derived script pubkey [4](#0-3) .
6. `sign` produces Schnorr shares for the forged prevout set, yielding a transaction that cannot spend the claimed funds [6](#0-5) .

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
