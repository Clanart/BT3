### Title
ReceivedOutput::read trusts attacker-supplied (offset, output) pairs without verifying the script_pubkey derives from the offset, letting crafted bytes cause outputs to be treated as spendable when they are not - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Grafana report describes a path traversal: crafted input steers a request to an unintended backend object. The analog in Serai is `ReceivedOutput::read`, which deserializes an `(offset, TxOut, OutPoint)` triple from untrusted bytes and accepts it unconditionally. The only place the binding between `offset` and `output.script_pubkey` is established is `Scanner::scan_transaction`, which looks up the script in `self.scripts`, a map built exclusively by `Scanner::new`/`register_offset` from `p2tr_script_buf(key + offset·G)`. `read` performs no such check, so bytes fed to it can claim any on-chain output is spendable under any scalar offset.

### Finding Description
`Scanner::scan_transaction` guarantees the invariant that a returned `ReceivedOutput`'s `offset` satisfies `output.script_pubkey == p2tr_tweaked(x_only(key + offset·G))` with an even-Y point, because it only emits outputs whose `script_pubkey` is a key in `self.scripts` (populated only by `register_offset`, which computes the script from the candidate offset and even rejects colliding scripts). [1](#0-0) 

`ReceivedOutput::read` bypasses that check entirely: it reads a `Secp256k1` scalar via `read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and constructs `ReceivedOutput { offset, output, outpoint }` with no consistency verification. [2](#0-1)  The struct's fields are private and there is no constructor that validates, so once bytes pass through `read`, the forgery is indistinguishable from a scanner-produced output. Downstream spend code consumes `output.offset()` to re-key the threshold keys (`ThresholdKeys::offset` adds the scalar to the group key at `crypto/dkg/src/lib.rs:414`), so the signing path is driven by exactly the attacker-controlled field. [3](#0-2) 

### Impact Explanation
An unprivileged party who supplies `ReceivedOutput` bytes (the documented ingestion path for externally-produced output claims) can:

- Report funds as received under an offset that does not derive the output's `script_pubkey`. The signer then re-keys by the wrong offset, producing a signature invalid for the actual Taproot output key — funds are credited as received but are permanently unspendable by the group.
- Pair a legitimate registered offset with an unrelated victim `TxOut`/`OutPoint`, directing a spend attempt at an output the group does not control, burning the real input's fees and producing an invalid transaction.

This is the accepted impact class "funds reported received that are not spendable": the traversal from untrusted bytes to the spend path skips the script↔offset binding that is the Bitcoin analog of the Grafana plugin's endpoint allowlist.

### Likelihood Explanation
Any channel that carries serialized `ReceivedOutput`s — coordinator-to-processor messages, DB restoration, or peer relay — exposes `read` to unprivileged bytes. The attacker needs no key material and no collusion; they only craft a scalar plus two consensus-encodable structures. Cost is trivial; the only requirement is that the deployment accepts `ReceivedOutput`s from a source other than the local `Scanner`.

### Recommendation
Either re-validate inside `ReceivedOutput::read`/`SignableTransaction` construction that `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)` for the known scan key, or make `read` crate-private so only `Scanner::scan_transaction`/`scan_block` can mint `ReceivedOutput`s. At minimum, document that `read` must only consume bytes previously produced by a trusted `Scanner` on the same key.

### Proof of Concept
```rust
// Attacker has the group's scan key `key` (public) and sees a real deposit
// legit_output paying to p2tr(x_only(key + o2*G)) for registered offset o2.
// They instead submit ReceivedOutput bytes claiming offset o1 (o1 != o2,
// or a scalar never registered) for that same TxOut/OutPoint.

let mut forged = Vec::new();
forged.extend((o1).to_bytes());            // wrong / unregistered offset
forged.extend(serialize(&legit_txout));    // output paying to o2's script
forged.extend(serialize(&outpoint));

let ro = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
// ro.offset() == o1, but ro.output().script_pubkey is derived from o2.
// Spend path re-keys ThresholdKeys by o1 -> group key becomes key + o1*G,
// which is NOT the taproot output key of the UTXO -> signature invalid,
// deposit counted as received yet unspendable.
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-214)
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
