### Title
`ReceivedOutput::read` installs an unvalidated spend offset from untrusted bytes, making received funds unspendable - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
Analogous to the Morpho report — where an unauthenticated `initialize` lets anyone install critical privileged state (the owner) — `ReceivedOutput::read` lets untrusted bytes install the most critical piece of spend state, the scalar `offset`, with no validation that it actually derives the key controlling the output's `script_pubkey`. The offset is the only secret needed (alongside the threshold keys) to spend a received output, yet deserialization performs zero binding between `offset`, `output.script_pubkey`, and the wallet's key.

### Finding Description
`ReceivedOutput::read` deserializes three independent fields — `offset` via `Secp256k1::read_F`, the `TxOut`, and the `OutPoint` — and returns them as a spendable output without ever checking that `key + G*offset` (plus the TapTweak and even-Y normalization done in `tweak_keys`/`register_offset`) maps to `output.script_pubkey`. [1](#0-0) 

The only place a valid offset is ever produced is `Scanner::register_offset`, which computes `p2tr_script_buf(self.key + GENERATOR * offset)` and stores the script→offset binding locally. [2](#0-1)  `scan_transaction` then produces `ReceivedOutput`s carrying that verified offset. [3](#0-2)  But once a `ReceivedOutput` round-trips through `serialize`/`read` — e.g., outputs relayed, backed up, or supplied by an untrusted party — that binding is lost. Any party supplying the bytes can substitute an arbitrary `offset` (or a mismatched `output`/`outpoint` triple), and the reader accepts it.

The spending path applies this offset to the threshold keys (`ThresholdKeys::offset`, added to `included[0]`'s secret share in `view`). [4](#0-3) [5](#0-4)  A substituted offset therefore silently re-keys the spend to a different private key — the wallet still believes it owns the output, and signing produces signatures valid for the wrong tweaked key, which the Bitcoin network rejects.

### Impact Explanation
Funds are reported as received (correct `TxOut`/`outpoint`) yet are not spendable: the signing set will run FROST over a key whose tweak does not correspond to the UTXO's `script_pubkey`, yielding unspendable signatures — a silent loss-of-funds availability condition matching the "funds reported received that are not spendable" acceptance criterion. Since `offset` is attacker-chosen, an attacker can also direct the tweak to a key they control only if they could also control the base key — they cannot — so the practical impact is denial of spendability plus wasted threshold-signing rounds.

### Likelihood Explanation
Requires an attacker to supply or tamper with serialized `ReceivedOutput` bytes consumed by `ReceivedOutput::read` (an explicitly in-scope untrusted input). No cryptographic break, no malicious validator, and no collusion is needed — only the ability to feed bytes to the deserialization API. Reachability depends on an integrator persisting or relaying `ReceivedOutput`s rather than consuming `Scanner` output directly, which is the natural usage given the `serialize`/`read` pair exists.

### Recommendation
Bind the offset to the output at deserialization or spend time: recompute `p2tr_script_buf(scanner_key + G*offset)` (with the same TapTweak/negation rules as `tweak_keys`) and reject the `ReceivedOutput` if it does not equal `output.script_pubkey`. Alternatively, store the `ReceivedOutput` keyed by `OutPoint` alongside a commitment to the offset so tampering is detectable.

### Proof of Concept
1. Honest wallet scans a funding TX: `scan_transaction` returns `ReceivedOutput { offset: o, output, outpoint }` where `p2tr(key + G*o) == output.script_pubkey`.
2. Attacker intercepts/alters the serialized form before `ReceivedOutput::read` (or crafts one wholesale): set `offset` to `o' = o + 1` (or any scalar), keep `output`/`outpoint` intact.
3. `ReceivedOutput::read` accepts it — no validation exists between `offset` and `output.script_pubkey`.
4. Wallet loads keys via `tweak_keys`/`offset(o')` and runs FROST signing; the aggregate signature verifies under `p2tr(key + G*o') ≠ script_pubkey`, so the spend fails — funds appear received but are unspendable.

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

**File:** crypto/dkg/src/lib.rs (L413-417)
```rust
  #[must_use]
  pub fn offset(mut self, offset: C::F) -> ThresholdKeys<C> {
    self.offset += offset;
    self
  }
```

**File:** crypto/dkg/src/lib.rs (L518-521)
```rust
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```
