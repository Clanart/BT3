### Title
Forged `ReceivedOutput` bytes are trusted as spendable inputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes `(offset, TxOut, OutPoint)` from attacker-controlled bytes with no authentication and no check that the `outpoint` exists on-chain or was produced by `Scanner` for the wallet key. A caller that consumes these bytes can therefore treat a nonexistent or unrelated UTXO as received funds.

### Finding Description
`ReceivedOutput::read` accepts a secp256k1 scalar offset, a consensus-decoded `TxOut`, and a consensus-decoded `OutPoint`, then returns `ReceivedOutput { offset, output, outpoint }` directly [1](#0-0) . The legitimate producer is `Scanner::scan_transaction`, which only creates an output when `output.script_pubkey` matches a registered script and derives `outpoint` from the containing transaction [2](#0-1) . The deserialization path bypasses both invariants. `SignableTransaction::new` then uses `input.output.value` for funding and `input.offset`/`input.outpoint` for the spend [3](#0-2) , while `SignableTransaction::multisig` checks only that `keys.offset(offset).group_key()` matches `prevouts[i].script_pubkey`, not that the referenced prevout exists [4](#0-3) .

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` can fabricate an apparently valid received output: choose the victim base key `K`, choose offset `o`, set `script_pubkey = p2tr(K + oG)`, set a large `value`, and set `outpoint` to a nonexistent txid/vout. The object passes `multisig`’s script check for that input, so the wallet reports balance and can even produce a signed transaction, but the input is not spendable because the prevout is not a real UTXO [4](#0-3) . This matches the accepted impact “funds reported received that are not spendable.”

### Likelihood Explanation
Reachability requires only that an application deserializes untrusted `ReceivedOutput` data instead of obtaining outputs from `Scanner` over verified blocks/transactions. The bug is not a cryptographic break; it is an authenticity/authorization gap at an untrusted parsing boundary explicitly exposed as `pub fn read` [1](#0-0) . Likelihood is moderate because some integrations may persist scanner outputs locally, but any path that imports outputs from peers, backups, indexers, or messages is exposed.

### Recommendation
Do not treat `ReceivedOutput::read` as proof of ownership. Either make the type private to scanner-produced outputs, or add a constructor/verifier that re-derives `p2tr_script_buf(key + generator * offset)` and requires it to equal `output.script_pubkey`, plus an explicit on-chain confirmation step before marking the output spendable [5](#0-4) [2](#0-1) . At minimum, document that deserialized `ReceivedOutput`s are unauthenticated and must be rescanned against confirmed blocks.

### Proof of Concept
Let the victim’s scanned base key be `K` and registered offset be `0`, so the expected script is `p2tr(K)`. An attacker serializes `ReceivedOutput { offset: 0, output: TxOut { value: 1_000_000, script_pubkey: p2tr(K) }, outpoint: OutPoint { txid: random_nonexistent, vout: 0 } }`. `ReceivedOutput::read` accepts it because it only parses fields [1](#0-0) . `value()` reports `1_000_000`, `SignableTransaction::new` counts it as input funding [3](#0-2) , and `multisig` accepts it since the script matches `K + 0G` [4](#0-3) . The resulting transaction signs a nonexistent prevout, so the reported funds are unspendable.

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
