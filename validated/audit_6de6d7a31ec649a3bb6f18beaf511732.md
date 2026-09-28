### Title
`ReceivedOutput::read` accepts an offset unrelated to the output's `script_pubkey`, producing unspendable "received" funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the upstream NULL-driver-name dereference (a lookup performed on an unvalidated field), `ReceivedOutput::read` deserializes an `offset` scalar, a `TxOut`, and an `OutPoint` without ever checking that `offset` is the HDKD offset that actually derives the key committed to in `output.script_pubkey`. The type is documented as "a spendable output," yet the reader constructs a value claiming spendability that the threshold wallet cannot satisfy.

### Finding Description
`Scanner::scan_transaction` is the honest producer of `ReceivedOutput`s: it only emits `(offset, output)` pairs where `output.script_pubkey` was looked up in `self.scripts`, i.e., `p2tr_script_buf(key + G*offset)` [1](#0-0) . That invariant — offset ↔ script correspondence — is what makes an output spendable, since spending a received input re-keys the FROST threshold keys by `offset` (the signer signs for `group_key + G*offset`, matching the Taproot script). `p2tr_script_buf` itself only produces scripts for even-Y keys [2](#0-1) .

`ReceivedOutput::read`, however, reads `offset` via `Secp256k1::read_F` and then consensus-decodes an arbitrary `TxOut`/`OutPoint`, returning the pair with no consistency check [3](#0-2) . Any caller that feeds untrusted bytes to `ReceivedOutput::read` (the documented untrusted-input sink) will accept an output whose offset does not derive the output's key.

### Impact Explanation
An attacker who can supply a serialized `ReceivedOutput` (or an `Output` wrapping one) claims funds were received that are not spendable: the value is reported (`value()`), but signing via `multisig`/offset re-keying produces a BIP-340 signature for `key + G*offset`, which does not match the x-only key in `output.script_pubkey`. The funds can never be moved; accounting that trusts the deserialized output overstates the wallet's spendable balance and may attempt doomed spends. This is a direct loss-of-funds-availability / false-balance condition reachable purely from attacker-controlled bytes — mirroring the upstream bug where a missing check on a looked-up field (`driver->name`) produced a crash during component lookup.

### Likelihood Explanation
Medium: the invariant is enforced at scan time but discarded at deserialization time, so any code path that round-trips attacker-influenced `ReceivedOutput` bytes (relay, cache, peer exchange of detected outputs) exposes it. No cryptographic assumptions are broken; the attacker only needs to write a canonical scalar plus a valid `TxOut`.

### Recommendation
In `ReceivedOutput::read`, verify consistency: derive `p2tr_script_buf(scanner_key + G*offset)` and compare to `output.script_pubkey`, or require the caller to pass the base key / expected script so the check can be performed at parse time. Reject mismatched inputs rather than constructing an unspendable output.

### Proof of Concept
```rust
// Serialize a ReceivedOutput whose offset does not match the script.
let honest = scanner.scan_transaction(&tx)[0].clone(); // offset = o, script for key + G*o
let evil = ReceivedOutput {
    offset: honest.offset() + Scalar::ONE, // wrong offset
    output: honest.output().clone(),       // script still commits to key + G*o
    outpoint: *honest.outpoint(),
};
let bytes = evil.serialize();
// Accepts without error, even though key + G*(o+1) != script_pubkey's key
let parsed = ReceivedOutput::read::<&[u8]>(&mut bytes.as_ref()).unwrap();
// Any spend attempt signs for the wrong key -> permanently unspendable.
```
(Fields are private; equivalently, hand-craft bytes: `write_all(offset_plus_one.to_repr()) || serialize(txout) || serialize(outpoint)`.)

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
