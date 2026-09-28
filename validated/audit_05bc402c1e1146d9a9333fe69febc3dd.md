### Title
ReceivedOutput deserialization accepts attacker-supplied offset/output bindings without verifying the output actually pays the offset key, enabling arbitrary outpoint/TxOut claims - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The reported bug class is a pass-through object lookup: an endpoint accepts an attacker-controlled identifier and returns an object belonging to another scope because the ownership check (`AttachmentSrv.visible`) is a no-op traversal. The analog in Serai is `ReceivedOutput::read`, which is the deserialization path for a "spendable output owned by this key" object. It reads three attacker-controlled fields — a scalar `offset`, a `TxOut`, and an `OutPoint` — and returns them as a coherent, owned, spendable `ReceivedOutput` without performing the one check that constitutes ownership: that `output.script_pubkey` equals `p2tr(group_key + offset * G)`. The constructor path (`Scanner::scan_transaction`) enforces this binding by only emitting outputs whose `script_pubkey` is in the registered `scripts` map; the `read` path discards that invariant entirely and is therefore a pass-through for arbitrary (outpoint, output, offset) triples.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` and `scan_transaction` only produces a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` hits, i.e., the offset is proven to correspond to the output's script. `ReceivedOutput::read`, however, performs `Secp256k1::read_F` for the offset and consensus-decodes `TxOut`/`OutPoint` with no consistency check between them — no recomputation of `key + offset * G`, no script comparison, no membership test. [1](#0-0) [2](#0-1)  Any downstream consumer trusting `offset()`, `value()`, or `outpoint()` (e.g., feeding the output into a `SignableTransaction` built by the `send` module, which offsets the threshold keys by `output.offset()` and signs for the input) inherits the forged binding. [3](#0-2) 

### Impact Explanation
An attacker who can deliver `ReceivedOutput` bytes to a wallet/processor (the sanctioned reachability for this scan) can cause either of the accepted impacts:
- **Funds reported received that are not spendable**: supply an `OutPoint`/`TxOut` paying a *different* party's script with a locally meaningful `offset`. The wallet reports `value()` as received balance, but `keys.offset(offset)` does not control that script, so the funds can never be spent and any spend transaction built on it is invalid or strands the wallet's accounting.
- **Value misattribution**: pair a real group-owned outpoint with a fabricated `TxOut` carrying an inflated `value`, corrupting balance/fee accounting downstream.
This mirrors the CVE exactly: the identifier (`outpoint`) selects an object belonging to another scope (another script owner), and the function that should scope it (offset↔script binding) is absent.

### Likelihood Explanation
Severity is Medium: exploitation requires a path where `ReceivedOutput` bytes cross a trust boundary (serialization for transport/storage between processor components), and impact is bounded to balance misreporting and unspendable-input selection rather than direct theft — the threshold signature still protects actual spends, so the worst concrete outcome is DoS of spending plus false crediting, consistent with the CVSS 6.0/Medium shape of the source report. It does not require validator collusion, key leakage, or unsafe code.

### Recommendation
Bind the offset to the output at the boundary. Either make `ReceivedOutput::read` take the `Scanner` (or the base key) and reject inputs where `output.script_pubkey != p2tr_script_buf(key + offset * G)` / is absent from `scripts`, or store only the `offset` and `outpoint` and re-derive the `TxOut` from chain data via `scan_transaction`. At minimum, document that `read` output must be re-validated against the Scanner before use in transaction construction.

### Proof of Concept
```rust
// Attacker-controlled bytes: an outpoint/TxOut paying *someone else's* P2TR script,
// paired with an offset valid for the victim group's key.
let mut bytes = vec![];
bytes.extend(offset_for_victim_key.to_bytes());           // valid scalar
bytes.extend(serialize(&TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: attacker_or_third_party_p2tr_script,   // NOT key + offset*G
}));
bytes.extend(serialize(&real_or_fake_outpoint));

let claimed = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// claimed.value() reports 1_000_000 sats as owned/spendable;
// keys.offset(claimed.offset()) cannot sign for claimed.output().script_pubkey.
// scan_transaction would never have emitted this output — read bypassed the check.
```
Contrast with `Scanner::scan_transaction`, which refuses the identical `(script, offset)` pair because `self.scripts.get(&output.script_pubkey)` misses.

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
