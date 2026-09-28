### Title
Scanner classifies any attacker-created output to internal offset scripts (Branch/Change/Forwarded) by script_pubkey alone - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` matches outputs purely on `output.script_pubkey` membership in `self.scripts`, a map that includes not only the external key's P2TR script but also every offset registered via `register_offset` — including the deterministic protocol-internal offsets for `OutputType::Branch`, `OutputType::Change`, and `OutputType::Forwarded` derived in `scanner()`. Any unprivileged Bitcoin user can compute and pay to these well-known scripts; the resulting output is reported with that offset (and hence that `OutputType`) with no check that the multisig itself created the funding transaction or intended the offset usage. This mirrors the advisory's bug class: an unauthenticated, attacker-supplied input is trusted and drives downstream action, because the received bytes were never authorized/validated against intent.

### Finding Description
`Scanner` maps `ScriptBuf -> Scalar` offset: [1](#0-0) 

`scan_transaction` returns a `ReceivedOutput` for *any* output whose `script_pubkey` hits the map, attributing the registered offset, with no provenance check: [2](#0-1) 

The processor-side `scanner()` registers the deterministic offsets `hash_to_F("Serai Bitcoin Output Offset", "branch" | "change" | "forward")` into the same script map: [3](#0-2) 

Because these offsets are deterministic hash outputs, anyone can compute `key + G*offset`, derive the P2TR script via `p2tr_script_buf`, and send an arbitrary `Transaction` paying to it. `get_outputs` then labels the attacker output `OutputType::Branch` / `Change` / `Forwarded` via `kinds[offset_repr_ref]`: [4](#0-3) 

`register_offset` documents that offsets are surjective and that arbitrary offsets can embed spendable script paths, but nothing restricts who may fund these scripts: [5](#0-4) 

### Impact Explanation
Outputs classified as `Change`/`Forwarded` are treated by the protocol as internally generated outputs. A `Forwarded`-kind output causes the processor to treat attacker funds as a forward-in-flight deposit, driving the threshold signing machines (`TransactionSignMachine::sign`) to produce signatures for forwarding/spending transactions the multisig never initiated — attacker-triggered signing of unintended transactions and fee expenditure. A `Change`-kind output corrupts accounting: funds that were never multisig change are booked as such, enabling balance misattribution and, where change flows feed new `SignableTransaction` inputs, coerced spending of attacker-chosen inputs under `Prevouts::All` sighash commitments. [6](#0-5) 

### Likelihood Explanation
The attack requires only sending a standard P2TR Bitcoin transaction — fully unprivileged, cheap (dust-level outputs are filtered only by `N::DUST` downstream, and even that can be exceeded trivially). The internal offset scripts are deterministic and publicly derivable from the group key plus the constant DST `"Serai Bitcoin Output Offset"`. No collusion, key material, or malformed encodings are needed.

### Recommendation
Bind scanned outputs to intent, not just script. Options: attach and require a per-output authenticated tag (e.g., a committed `data`/`InInstruction` or a distinct offset domain per output purpose) before attributing `Branch`/`Change`/`Forwarded` kinds; treat unrecognized deposits to internal offset scripts as `External`-kind (or discard) rather than internal kinds; or scope `kinds` lookups so that internally-significant offsets only apply to outputs from transactions the multisig itself created.

### Proof of Concept
```rust
// Attacker side (only needs the multisig's group key, which is public):
let key: ProjectivePoint = multisig_group_key();
let fwd_offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"forward");
let fwd_script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * fwd_offset).unwrap();
// Broadcast a normal tx paying >= DUST sats to fwd_script.

// Processor side: in get_outputs, scanner.scan_transaction(tx) returns the output
// with offset == fwd_offset; kinds[fwd_offset_repr] == OutputType::Forwarded.
// The output is emitted as a protocol-internal forwarded output, feeding the
// forwarding/signing pipeline as if the multisig had created it itself.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L153-165)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L168-179)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
        )?;
```
