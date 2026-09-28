### Title
ReceivedOutput::read accepts an arbitrary scalar offset without checking it corresponds to the output's script_pubkey — unspendable funds reported as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` pairs a scalar `offset` (the private key material needed to spend) with a `TxOut`/`OutPoint`, yet `ReceivedOutput::read` deserializes both fields from attacker-controlled bytes with no consistency check that `key + offset·G` actually produces `output.script_pubkey`. This mirrors the CVE-2019-10332 class: a missing authorization/consistency check on an entry point reachable with untrusted input lets an external party cause the wallet to act on data that does not correspond to any spendable key.

### Finding Description
`ReceivedOutput::read` reads a `Secp256k1` scalar via `Secp256k1::read_F`, then a `TxOut` and `OutPoint` via consensus decoding, and returns them directly [1](#0-0) . The only place an offset is ever bound to a script is inside `Scanner`, which derives scripts as `p2tr(key + offset·G)` and only creates `ReceivedOutput`s for outputs whose `script_pubkey` is in its `scripts` map [2](#0-1) . That binding exists solely in `Scanner`'s private in-memory map [3](#0-2)  — `ReceivedOutput` itself carries no key or script commitment, so deserialization performs no check equivalent to the scanner's `self.scripts.get(&output.script_pubkey)` lookup [4](#0-3) . Any `ReceivedOutput` reconstructed from untrusted bytes (via `read`/`serialize` round-trips across storage or message boundaries) can therefore claim an arbitrary offset for an arbitrary output.

### Impact Explanation
An unprivileged party who can supply bytes to `ReceivedOutput::read` (e.g., output data relayed to a Serai processor/wallet) can report an output as received while pairing it with an incorrect or attacker-chosen offset. The wallet then believes it holds spendable funds — `value()`, `offset()`, and `outpoint()` all appear valid [5](#0-4)  — while the spend path derives a key that does not control the output's `script_pubkey`, or worse, the `offset` may correspond to a key/script path the attacker selected (register_offset's docs warn that arbitrary offsets "may introduce a script path into the output, allowing the output to be spent by satisfaction of an arbitrary script" [6](#0-5) ). The result is funds reported received that are not spendable by the intended key, satisfying the Medium-severity impact class.

### Likelihood Explanation
Exploitation requires an attacker to influence the byte stream deserialized by `ReceivedOutput::read`. `read`/`serialize` are public, format-fixed functions intended for persistence and transport [7](#0-6) ; in the Serai architecture, received outputs flow from scanning into the processor/signing pipeline, so any channel feeding deserialized `ReceivedOutput`s (peer-supplied output claims, DB entries populated from network data) is a viable vector. The bytes needed are fully public knowledge (any real on-chain `TxOut`/`OutPoint` plus a 32-byte scalar), requiring no privileges — matching the "Overall/Read access" low bar of the original advisory.

### Recommendation
Bind the offset to the output at the `ReceivedOutput` boundary. Either store the scanner's base `key` (or the derived P2TR script) in `ReceivedOutput` and verify `p2tr_script_buf(key + offset·G) == output.script_pubkey` inside `read` before returning, or make `read` take the expected `ScriptBuf`/`Scanner` so deserialization fails closed on any offset that does not reproduce the output's `script_pubkey`.

### Proof of Concept
1. Attacker picks any confirmed `TxOut`/`OutPoint` paying to a script the victim cannot spend via the derived offset (e.g., an output to the untweaked scanner key while supplying `offset = 1`, or an output whose registered scanner offset is `o` while supplying `o + 1`).
2. Serialize `offset.to_bytes() || serialize(TxOut) || serialize(OutPoint)` and feed it to `ReceivedOutput::read` [8](#0-7) .
3. `read` returns `Ok` — no error is possible since no offset↔script check exists. Downstream code treats the output as spendable (`value()` reports real satoshis), but signing with `offset` produces a key that does not satisfy `output.script_pubkey`, so the funds are unspendable despite being reported received.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L100-118)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L153-156)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L177-179)
```rust
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```
