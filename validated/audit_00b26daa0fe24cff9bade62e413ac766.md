### Title
`ReceivedOutput::read` bypasses the offset↔`script_pubkey` consistency invariant enforced by `Scanner`, allowing unspendable outputs to be reported as received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The n8n advisory describes a restriction ("Allowed HTTP Request Domains") enforced on one code path (the HTTP Request node) but skipped on an alternate path (the GraphQL node), letting an attacker reach resources the policy was meant to exclude. The same shape exists in `bitcoin-serai`: the `Scanner` is the only path that can legitimately produce a `ReceivedOutput`, and it does so by construction — it only records an output when `output.script_pubkey` is present in its `scripts` map, guaranteeing that `offset` actually derives the output's key (`key + offset·G`, taproot-tweaked). `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` as three independent, attacker-controlled fields with no check that `p2tr_script_buf(key + offset·G) == output.script_pubkey`. An untrusted byte stream therefore mints a `ReceivedOutput` the scanner would never have produced, breaking the spendability invariant.

### Finding Description
`Scanner::register_offset` inserts `(script, offset)` pairs only after deriving the script from the key plus offset and confirming the point is even (i.e., `p2tr_script_buf` returns `Some`), so every `offset` in `scripts` provably corresponds to its `script_pubkey` [1](#0-0) . `Scanner::scan_transaction` only emits a `ReceivedOutput` when a real transaction output's `script_pubkey` matches a registered script, so the returned `offset` is guaranteed to yield a key that can spend that exact output [2](#0-1) . `ReceivedOutput::read`, the alternate ingestion path for untrusted bytes, reads an arbitrary scalar via `Secp256k1::read_F` and an arbitrary `TxOut`/`OutPoint` via consensus decode, and returns them as a valid `ReceivedOutput` without verifying the offset derives the output's script, without verifying the output is a P2TR output at all, and without checking the derived point's parity [3](#0-2) . There is no stored key/`scripts` context on `ReceivedOutput`, so the consistency check performed implicitly by the scanner is entirely absent on the deserialization path.

### Impact Explanation
`ReceivedOutput` is the wallet's representation of spendable, received funds: `value()`, `offset()`, `output()`, and `outpoint()` are the inputs a spender uses to build and sign a transaction [4](#0-3) . A forged `ReceivedOutput` whose `script_pubkey` does not equal `p2tr_script_buf(key + offset·G)` (or which is not P2TR, or whose `offset` yields an odd/non-tweakable point) will be counted as received balance yet cannot be spent — the key path signature produced with `offset` does not control the referenced output, or the referenced `outpoint` pays to an unrelated script entirely. This yields the accepted impact class of funds reported received that are not spendable, and can also drive futile signing rounds over attacker-chosen offsets/inputs. Worse, an attacker who observes the scanner-registered offsets can craft an output paying to a *different* script (e.g., their own address or a script-path output — the scanner itself warns that arbitrary offsets "introduce a script path" spendable by a script, not the key [5](#0-4) ) while pairing it with a valid-looking offset, corrupting accounting and spend planning.

### Likelihood Explanation
`ReceivedOutput::read` exists precisely to ingest serialized outputs from storage or peer/coordinator messages — the same untrusted-byte sink class the scan scope designates as reachable. Any party able to supply or tamper with serialized `ReceivedOutput`s (a peer relaying detected outputs, corrupted/untrusted storage, a malicious coordinator component) reaches the vulnerable path with only public inputs; no key material, threshold cooperation, or validator privileges are required. The scanner path remains correct, so exploitation is confined to deployments that accept `ReceivedOutput`s from sources other than their own `Scanner::scan_*` calls — which is the intended use of a public `read` function. Impact is integrity/availability of funds accounting rather than key recovery, fitting Medium.

### Recommendation
Bind the deserialization path to the same invariant the scanner enforces. Either:
- Make `ReceivedOutput::read` take the scanning key (or the `Scanner`) and reject any `ReceivedOutput` where `p2tr_script_buf(key + ProjectivePoint::GENERATOR * offset) != Some(output.script_pubkey)`, mirroring `register_offset`'s derivation [6](#0-5) ; or
- If key context is unavailable at read time, document `read` as producing an *unverified* output and add a `verify(&self, key: ProjectivePoint) -> bool` that spenders must call before counting or spending the output, also rejecting outputs whose derived point is odd (`p2tr_script_buf` returning `None` [7](#0-6) ).

### Proof of Concept
1. Let `K` be the threshold group's tweaked `ThresholdKeys<Secp256k1>` group key and `s = p2tr_script_buf(K)` the script the honest `Scanner` watches.
2. An attacker chooses an arbitrary scalar `offset'` (e.g., `Scalar::ONE`) and computes `K' = K + G·offset'` — or simply picks an unrelated P2TR script `s'` paying to themselves.
3. The attacker serializes `offset'`, a `TxOut { value: 1_000_000, script_pubkey: s' }`, and an `OutPoint` for a real (attacker-funded or unrelated) output, and feeds the bytes to `ReceivedOutput::read`.
4. `read` returns `Ok(ReceivedOutput { offset: offset', output, outpoint })` — no error, despite `Scanner::scan_transaction` never emitting this pair because `s'` is absent from `scripts` [8](#0-7) .
5. The consumer treats `value()` = 1,000,000 sats as received and attempts to spend via `offset()`; the produced key-path signature does not authorize `s'` (which may carry a script path or belong to the attacker), so the funds are unspendable / the transaction is invalid — exactly the class of restriction-enforced-on-one-path, bypassed-on-another from the reference advisory.

Note: verification of the downstream spend path (`send.rs` sighash construction) was not fully performed; the claim relies on the demonstrated asymmetry between `Scanner` (derives offset from script) and `ReceivedOutput::read` (accepts both independently).

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

**File:** networks/bitcoin/src/wallet/mod.rs (L177-179)
```rust
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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
