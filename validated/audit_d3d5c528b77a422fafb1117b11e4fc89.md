### Title
`ReceivedOutput::read` accepts an unverified offset↔output pairing, allowing funds to be reported received that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The kernel bug freed an I2C adapter while `drm_connector.ddc` kept pointing at it — a binding between two objects that was silently allowed to go stale. The analog in `bitcoin-serai` is the `ReceivedOutput` type: it binds a scalar `offset` (the key-derivation offset needed to spend) to a `TxOut`/`OutPoint` (the thing being spent). `Scanner` only ever produces consistent pairings, but `ReceivedOutput::read` deserializes the `offset`, `output`, and `outpoint` as three independent fields with no check that `output.script_pubkey == p2tr(key + offset·G)` [1](#0-0) . The offset is a "dangling reference" to a key the output was never actually locked to.

### Finding Description
`Scanner::scan_transaction` constructs `ReceivedOutput` by looking up `self.scripts[&output.script_pubkey]`, guaranteeing the stored offset is exactly the registered offset whose tweaked key produced that script [2](#0-1) . That invariant — `offset` corresponds to `script_pubkey` — exists only in the in-memory `HashMap` and is not re-established on deserialization. `read()` accepts an arbitrary scalar via `Secp256k1::read_F`, an arbitrary `TxOut`, and an arbitrary `OutPoint` [1](#0-0) .

Downstream, `SignableTransaction::new` blindly trusts the pairing: it collects `inputs.iter().map(|input| input.offset)` to derive the per-input signing keys while using `input.outpoint`/`input.output` for the transaction's actual prevouts [3](#0-2) . Each input's `AlgorithmSignMachine` is built on keys offset by the claimed scalar and signs the Taproot key-spend sighash for that prevout [4](#0-3) . If the claimed offset does not match the output's `script_pubkey`, the aggregated signature is invalid for that input: the output is unspendable even though it was counted as received funds (`input_sat` includes its value).

### Impact Explanation
Any consumer that feeds `ReceivedOutput::read` untrusted or corrupted bytes (DB restoration, network-provided output claims, forwarded outputs between Serai components) can produce an output that is accepted into the wallet's spendable set yet can never be signed for — the signature verification under `key + offset·G` fails against the actual Taproot script. This is "funds reported received that are not spendable": the wallet can strand balance, and since `SignableTransaction` builds a real transaction paying fee on these inputs, a failed/partial spend attempt also wastes fees and can wedge scheduling. More subtly, because offsets are surjective under `register_offset` (odd tweaked keys are incremented) [5](#0-4) , a mismatched offset isn't merely "wrong key" — an attacker who knows registered offsets can pick one that maps to a *different registered script*, mislabeling which output an offset belongs to.

### Likelihood Explanation
Reachable wherever `ReceivedOutput` bytes cross a trust boundary. The struct exposes `serialize()`/`read()` precisely for persistence and transport [6](#0-5) , so any pipeline that round-trips outputs through storage or messages an untrusted party can influence is exposed. No cryptographic break is needed — only the ability to supply or corrupt serialized output records.

### Recommendation
Bind the offset to the output at deserialization time. `ReceivedOutput::read` should either take the scanning key and assert `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)`, or store/serialize the derived script and compare. Absent that, `SignableTransaction::new` should re-verify each input's `offset` against `input.output.script_pubkey` before collecting offsets and building per-input sign machines [7](#0-6) .

### Proof of Concept
```
// key: the multisig group key (already even per tweak_keys)
let mut scanner = Scanner::new(key).unwrap();
// Legitimate received output at offset ZERO
let real = scanner.scan_transaction(&funding_tx)[0].clone();

// Attacker-crafted record: same TxOut/OutPoint, but a bogus offset
let mut forged_bytes = Vec::new();
forged_bytes.extend(Scalar::from(7u64).to_bytes());   // offset != real offset
forged_bytes.extend(serialize(real.output()));        // real script_pubkey
forged_bytes.extend(serialize(real.outpoint()));
let forged = ReceivedOutput::read(&mut forged_bytes.as_slice()).unwrap(); // accepted

// SignableTransaction::new counts forged.value() as spendable input and signs
// input 0 under key + 7·G — but the output is locked to key + real_offset·G.
// The resulting transaction's witness is invalid; the "received" funds cannot
// be spent. read() performed no consistency check between the three fields.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
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
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L205-210)
```rust
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
