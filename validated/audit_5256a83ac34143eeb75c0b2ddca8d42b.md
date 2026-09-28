### Title
`ReceivedOutput::read` deserializes an unverified (offset, output) binding, letting attacker-crafted bytes claim unspendable outputs as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` couples a scalar `offset` with a `TxOut`/`OutPoint`. The only legitimate producer is `Scanner::scan_transaction`, which guarantees the output's `script_pubkey` equals `p2tr(key + offset)` by construction. `ReceivedOutput::read` accepts untrusted bytes and reconstructs that pairing with no consistency check, so the data-validation invariant the rest of the wallet relies on is not enforced on deserialization.

### Finding Description
`Scanner::scan_transaction` only creates a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns the registered offset, i.e., the script_pubkey is verified to equal `p2tr_script_buf(key + offset)` [1](#0-0) . The script→offset map is only populated through `Scanner::new`/`register_offset`, which compute the script from the key and offset [2](#0-1) .

`ReceivedOutput::read` instead reads the `offset` scalar, then consensus-decodes an arbitrary `TxOut` and `OutPoint` and returns them verbatim [3](#0-2) . It never checks that `output.script_pubkey == p2tr_script_buf(key + offset)` — it cannot, because the struct doesn't even retain the key. The deserialized object is therefore indistinguishable from a legitimately scanned output: `offset()` [4](#0-3) , `output()` [5](#0-4) , and `value()` [6](#0-5)  will all report the attacker's claims. Any downstream code that feeds the output into `send.rs` signing will produce a signature under `key + offset` that does not control the output's actual script_pubkey.

### Impact Explanation
Funds reported received that are not spendable. An unprivileged party who can get crafted bytes fed to `ReceivedOutput::read` (e.g., forged scan results, a malicious or corrupted sync payload) causes the wallet to record a received output whose declared `offset` does not match the real `script_pubkey`. The wallet credits `value()` to its balance, yet spending is impossible: the key derived as `key + offset` does not satisfy the output's actual script. Worse, since `offset` need not correspond to a `p2tr` output of `key + offset` at all, the attacker can select offsets that embed an arbitrary script path — the exact scenario `register_offset`'s own documentation warns about ("arbitrary offsets may introduce a script path into the output, allowing the output to be spent by satisfaction of an arbitrary script (not by the signature of the key)") [7](#0-6) . This is the same class as the reference report: a security-relevant binding is trusted on input rather than re-validated at the deserialization boundary.

### Likelihood Explanation
Reachability requires that serialized `ReceivedOutput`s cross a trust boundary — e.g., stored scan results, processor messages, or backups an attacker can influence — which is exactly the input class this scanner design anticipates. No cryptographic break, collusion, or privileged position is needed; only control over the bytes passed to `read`. Severity is medium-to-high depending on integrator usage: funds are not directly stolen (the attacker cannot spend to himself unless he also registered a script-path-bearing offset the victim later signs against an output whose script he controls), but balance inflation and permanent freezing of accounted funds are concrete.

### Recommendation
Re-validate the (key, offset, script_pubkey) relationship at the deserialization boundary: either store the scan key in `ReceivedOutput` and check `output.script_pubkey == p2tr_script_buf(key + offset)` inside `read`, or expose a `verify(key)` method and require callers to invoke it on any deserialized output. Alternatively, serialize the canonical scan product (key commitment + offset + outpoint) and re-derive the `TxOut` binding rather than trusting it.

### Proof of Concept
```rust
use bitcoin::{OutPoint, ScriptBuf, TxOut, Amount, Txid, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;
use frost::curve::Secp256k1;
use ciphersuite::Ciphersuite;

// Attacker crafts bytes: arbitrary offset + TxOut whose script_pubkey is NOT
// p2tr(key + offset), e.g. a script paying the attacker.
let mut bytes = Vec::new();
bytes.extend(Scalar::ONE.to_bytes()); // offset = 1
let evil = TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: ScriptBuf::new_op_return(b"attacker-controlled"),
};
bytes.extend(bitcoin::consensus::encode::serialize(&evil));
bytes.extend(bitcoin::consensus::encode::serialize(
    &OutPoint::new(Txid::all_zeros(), 0),
));

// Deserialization succeeds despite the offset/script mismatch.
let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(ro.value(), 1_000_000);       // wallet credits 1M sats
assert_eq!(ro.offset(), Scalar::ONE);    // under offset 1 — unspendable pairing
```
The check `self.scripts.get(&output.script_pubkey)` in `scan_transaction` is the only place this binding is enforced; `read` bypasses it entirely.

Caveat: I could not inspect the callers of `ReceivedOutput::read` (processor/DB layer is outside the indexed scope), so the exact delivery path for attacker bytes is inferred from the API's public exposure rather than a confirmed call site.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L101-103)
```rust
  pub fn offset(&self) -> Scalar {
    self.offset
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L106-108)
```rust
  pub fn output(&self) -> &TxOut {
    &self.output
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L116-118)
```rust
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
