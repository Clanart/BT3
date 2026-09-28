### Title
`ReceivedOutput::read` deserializes attacker-controlled (offset, TxOut, outpoint) triples without verifying the output is actually addressed to the scanned key — funds reported received that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Koel report describes a validation gap between two routes that create the same object: the web API validates the URL, the Subsonic route does not, and the unvalidated value is later acted on server-side. `bitcoin-serai` has the same shape: `Scanner::scan_transaction` only yields `ReceivedOutput`s whose `script_pubkey` provably matches a registered key offset, while `ReceivedOutput::read` accepts an attacker-supplied `(offset, TxOut, OutPoint)` triple with no check that the output's `script_pubkey` corresponds to `key + offset*G`. Untrusted bytes therefore produce a `ReceivedOutput` that reports received funds which the group's key cannot spend.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` mapping each P2TR script to the offset that derives it, populated via `register_offset`, which computes `p2tr_script_buf(self.key + GENERATOR * offset)` [1](#0-0) . `scan_transaction` only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` hits, so the `offset` field is guaranteed to correspond to the output's script [2](#0-1) .

`ReceivedOutput::read` bypasses this invariant entirely: it reads a raw scalar via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and constructs `ReceivedOutput` with no key or script binding [3](#0-2) . There is no `Scanner`-aware deserialization (no `read` variant that takes the scanner/key), so any integrator receiving `ReceivedOutput`s over the wire — e.g., a coordinator distributing detected deposits to signers — gets objects whose offset may be unrelated to the claimed output. `read_F` only enforces canonical scalar encoding, not semantic validity [4](#0-3) .

### Impact Explanation
A `ReceivedOutput` whose `offset` does not match `output.script_pubkey` claims the output is spendable by `tweak_keys(keys.offset(offset))` when it is not. Downstream, `send.rs` builds a `SignableTransaction` from these outputs; the signers will produce a Schnorr signature under the wrong key, yielding a transaction that either cannot broadcast (funds "received" per the bookkeeping but unspendable — the accepted impact category) or, if the attacker picks an `offset` under their own key relative to a group script, mis-attributes deposits. It also enables inflating reported balances: an attacker supplies a `ReceivedOutput` referencing any on-chain `OutPoint`/`TxOut` with a large `value`, and consumers that trust the deserialized object record funds the Serai key never received and cannot spend.

### Likelihood Explanation
Reachability requires only that untrusted bytes reach `ReceivedOutput::read` — explicitly an in-scope input surface per the rules (serialization formats exist precisely for cross-party transport of scanned outputs). No malicious validator or leaked key is needed; the attacker crafts `serialize()`-compatible bytes choosing arbitrary `offset`, `TxOut`, and `OutPoint`. Severity is bounded because exploit payoff depends on integrator behavior (credited deposits, attempted spends), so Medium–High rather than unconditional fund theft.

### Recommendation
Add a validated deserialization path, e.g. `Scanner::read_received_output(reader)` (or `Scanner::verify(&ReceivedOutput)`), that checks `self.scripts.get(&output.script_pubkey) == Some(offset)` before accepting the object — mirroring the scan-time invariant. Document `ReceivedOutput::read` as producing unverified data, and have consumers (processor/signers) only accept outputs returned through scanner verification.

### Proof of Concept
```rust
// Attacker crafts bytes for a ReceivedOutput pointing at an output
// the group key cannot spend.
let evil_offset = Scalar::from(1u64); // unrelated to any registered script
let output = TxOut {
    value: Amount::from_sat(1_000_000_000),
    script_pubkey: victim_p2pkh_or_anyone_script, // not key + evil_offset*G
};
let outpoint = OutPoint::new(real_txid, 0);
// serialize: offset.to_bytes() || serialize(output) || serialize(outpoint)
// feed to:
let ro = ReceivedOutput::read(&mut bytes).unwrap();
assert_eq!(ro.value(), 1_000_000_000); // reported as a received, "spendable" output
// Scanner::scan_transaction would never have emitted this output,
// since script_pubkey is not in self.scripts.
```
Verification the fix works: `Scanner::read_received_output` must reject this because `self.scripts.get(&output.script_pubkey)` returns `None` (or a different offset), while `ReceivedOutput::read` today accepts it silently.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
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
      }
    }
    res
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L74-83)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }
```
