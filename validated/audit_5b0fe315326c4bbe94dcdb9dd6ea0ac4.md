### Title
`ReceivedOutput::read` does not check the offset/script_pubkey binding, allowing deserialized outputs which are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
CVE-2019-16089 is a missing check on the return of `nla_nest_start_noflag`, letting a malformed message propagate. The analog in `networks/bitcoin/src/wallet/mod.rs` is `ReceivedOutput::read`, which deserializes the three fields (`offset`, `output`, `outpoint`) independently and never checks that `offset` actually derives `output.script_pubkey` under the scanner's key — the invariant the rest of the wallet relies on to spend.

### Finding Description
When the scanner constructs a `ReceivedOutput` internally via `scan_transaction`, the `offset` is guaranteed to satisfy `p2tr_script_buf(key + offset*G) == output.script_pubkey`, because the offset is looked up in `self.scripts` keyed by that exact script [1](#0-0) . That binding is the only thing that makes the output spendable: `register_offset` documents that an offset introduces the key (and possibly a script path) needed to spend the output [2](#0-1) .

`ReceivedOutput::read`, however, just decodes `offset` via `Secp256k1::read_F` and decodes the `TxOut`/`OutPoint` blobs, returning the struct with no cross-field validation [3](#0-2) . Any byte stream that decodes successfully is accepted, including one where `offset` corresponds to a different (or unspendable/odd) key than the one embedded in `output.script_pubkey`. Like the kernel bug, a structural field that must be validated is trusted unchecked, and the malformed object is propagated to callers.

### Impact Explanation
A `ReceivedOutput` whose `offset` does not match its `script_pubkey` produces a spend key that does not control the output. Any code feeding untrusted/foreign `ReceivedOutput` bytes into the wallet (the type is the unit by which scanned funds are carried into `send.rs` for signing) will treat the output as received funds and attempt to spend it; the resulting transaction either cannot be constructed (odd/infinity derived key) or produces a signature under a key that does not match the output, rendering the "received" funds unspendable. This is an integrity/availability failure on the funds-reporting path, matching the "funds reported received that are not spendable" acceptance criterion.

### Likelihood Explanation
Reachable only where `ReceivedOutput::read` is applied to bytes not produced by the local `Scanner` — e.g., outputs relayed between scanners, restored from an attacker-influenced store, or injected into a signing pipeline. Within a single trusted process scanning its own chain data, the invariant holds by construction, so the likelihood is conditional on deserialization of external data, hence Medium rather than higher.

### Recommendation
Either make `ReceivedOutput` an opaque type that can only be constructed by `Scanner` (removing the public unchecked `read`, or requiring the scanner key on `read` and verifying `p2tr_script_buf(key + offset*G) == output.script_pubkey` before returning `Ok`), or document and enforce the check at the point where deserialized outputs are admitted into the spend pipeline.

### Proof of Concept
Conceptual: take a legitimately scanned `ReceivedOutput` serialization, then replace the `script_pubkey` inside the encoded `TxOut` with a different valid P2TR script (any 32-byte x-only key). `ReceivedOutput::read` accepts it since each field decodes independently; the resulting value claims spendability via `offset` while the offset derives a different key. Spending it via `sign_transaction`/`ReceivedOutput`-driven send flow yields an output the threshold key cannot actually spend. I was unable to fully verify the downstream send path in `networks/bitcoin/src/wallet/send.rs` within this session, so the exact failure mode at spend time (panic vs. invalid signature) is inferred from the documented offset semantics rather than confirmed end-to-end.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L121-134)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L168-196)
```rust
  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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
