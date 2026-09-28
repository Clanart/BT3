### Title
Untrusted bytes to `ReceivedOutput::read` can credit an output under an arbitrary scalar offset with a spendable script path - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Covalent bug is a missing authorization/beneficiary check: a function accepts a caller-supplied destination (`beneficiary`) without verifying the caller is entitled to it, letting anyone redirect assets. The Serai analog is `ReceivedOutput::read`, which accepts a caller-supplied scalar `offset` — the "which key/script this output belongs to" field — with no check that the offset is one the scanner legitimately registered. Because `register_offset` explicitly warns that arbitrary offsets "may introduce a script path into the output, allowing the output to be spent by satisfaction of an arbitrary script," a deserialized `ReceivedOutput` can reference an offset whose Taproot output is spendable by an attacker-controlled script path rather than by the threshold key.

### Finding Description
`ReceivedOutput` consists of an `offset` scalar, a `TxOut`, and an `OutPoint`, and `read` deserializes all three directly from untrusted bytes via `Secp256k1::read_F` and consensus decoding, with no binding between the offset and the scanner's registered offset set (`Scanner::scripts`).

```rust
// networks/bitcoin/src/wallet/mod.rs:122-134
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    ...
    Ok(ReceivedOutput { offset, output, outpoint })
}
```

Contrast with `scan_transaction`, which only produces `ReceivedOutput`s whose offset came from the registered `self.scripts` map (`networks/bitcoin/src/wallet/mod.rs:199-214`). The `register_offset` documentation states offsets "must be securely generated" because arbitrary offsets can introduce a spendable script path (`networks/bitcoin/src/wallet/mod.rs:177-179`). `read` bypasses that invariant entirely: it manufactures a `ReceivedOutput` claiming funds were received to key `key + offset·G` for *any* scalar, including offsets an attacker chose precisely so the tweaked Taproot commitment embeds a script path they control.

### Impact Explanation
A deserialized `ReceivedOutput` is treated downstream as "funds received and spendable by the multisig under `keys.offset(offset)`." An attacker who feeds crafted bytes to `ReceivedOutput::read` can report an output as received under an offset whose P2TR output has a known script path — the real on-chain output is spendable by the attacker's script, not by a threshold signature, so the system credits funds that are not spendable (or are stealable by the attacker). This mirrors the original bug's shape: an attacker-chosen beneficiary/offset field is trusted without checking it belongs to the legitimate set.

### Likelihood Explanation
Reachable only where `ReceivedOutput::read` consumes attacker-influenced bytes (e.g., serialized eventuality/output data relayed between components rather than produced by the local `Scanner`). If the only producer is the local scanner over confirmed blocks, the offset always comes from `register_offset` and the issue is unexploitable — that path could not be fully confirmed within this pass, so the exploitability depends on the deserialization entry point being reachable with untrusted data, which the prompt's reachability rules treat as in scope.

### Recommendation
Make `ReceivedOutput::read` verify the supplied offset against the scanner's registered set — e.g., take the `Scanner` (or its `scripts` map) as a parameter and reject offsets whose derived `p2tr_script_buf` doesn't match the output's `script_pubkey`, or confine `read` to trusted serialization contexts and expose a separate verified constructor for untrusted input.

### Proof of Concept
Conceptually: an attacker chooses a scalar `offset` such that `key + offset·G` tweaks to a Taproot output committing to a script tree containing `attacker_pubkey OP_CHECKSIG`. They register/construct a real Bitcoin output paying to that script (or reference an existing one), serialize `ReceivedOutput { offset, output, outpoint }`, and feed the bytes to `ReceivedOutput::read`. The result is accepted as a group-received output, but `key + offset·G` is spendable via the attacker's script path — the credited funds are not spendable by the threshold key. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L174-196)
```rust
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
