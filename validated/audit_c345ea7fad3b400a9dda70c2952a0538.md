### Title
`ReceivedOutput::read` accepts an arbitrary (offset, script_pubkey) pair, so `Output::key` derives a key inconsistent with the spendable key - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The cohttp bug was a *validate-on-encoded-form, decode-later* ordering flaw: dot-segment normalization ran on the raw path, and `%2f` was only decoded afterward, smuggling separators past the check. The same class exists in `bitcoin-serai`'s output handling: `ReceivedOutput::read` deserializes a scalar `offset` and a `TxOut` independently, never checking that `offset` actually corresponds to the output's `script_pubkey`. The consistency relation (`script_pubkey == p2tr_script_buf(key + offset*G)`) is only established inside `Scanner::scan_transaction` at scan time; the `read` path decodes untrusted bytes and skips that check entirely. Downstream, `Output::key()` decodes the script's x-only key (assuming `Parity::Even`) and subtracts `offset*G` to "recover" the owning key — silently producing a wrong key for a crafted encoding.

### Finding Description
`Scanner::scan_transaction` only produces `ReceivedOutput`s whose `offset` came from the `scripts` map, so scan-derived outputs are internally consistent. [1](#0-0) 

But `ReceivedOutput::read` accepts attacker-controlled bytes: [2](#0-1) 

It reads `offset = Secp256k1::read_F(r)` and a consensus-decoded `TxOut` with no check that `key + offset*G` maps to `output.script_pubkey` — the decode step introduces semantic structure (a claimed key-ownership relation) that was never validated. `Output::key()` then assumes that relation holds: [3](#0-2) 

`Secp256k1::read_G` rejects odd-parity reconstruction issues, but the script's x-only key is decoded under the assumed `Parity::Even`, and `offset` is whatever the bytes claimed. Since `p2tr_script_buf` requires an even Y coordinate, a crafted `offset` differing from the true one yields a completely different "group key" rather than an error — the corrupted relation passes through the decode undetected, exactly like the `%2f` surviving normalization.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (a sink explicitly reachable via processor deserialization of outputs, `Output::read` at `processor/src/networks/bitcoin.rs:145-166`) can cause the processor to report funds as received under a group key that does not control them: `Output::key()` returns `script_key - offset*G`, which for a mismatched `offset` is a key no one holds a share for, or a key belonging to a different indexed output type (`kinds` mapping in `scanner()` at `processor/src/networks/bitcoin.rs:314-346` is keyed by the claimed offset). The result is funds reported received that are not spendable by the claimed key — or outputs misattributed between External/Branch/Change/Forwarded kinds — which downstream balance/credit logic treats as real.

### Likelihood Explanation
Reachability requires an attacker to supply serialized `Output`/`ReceivedOutput` bytes rather than going through `scan_transaction`. Where the processor trusts only scanner-produced data this is unreachable, but the type's `read` is a public deserialization API over untrusted bytes and the consistency invariant is enforced nowhere in it — any integration or peer-supplied data path that deserializes outputs inherits the flaw. Moderate likelihood, Medium severity.

### Recommendation
Either make `ReceivedOutput` an opaque type only constructible by `Scanner` (privately constructing it in `read` and re-deriving consistency against a provided key), or have `ReceivedOutput::read` take the expected group `key` and verify `p2tr_script_buf(key + GENERATOR*offset) == output.script_pubkey`, rejecting otherwise. At minimum, `Output::key` should validate the parity/consistency assumption instead of blindly subtracting a claimed offset.

### Proof of Concept
```rust
// Key the wallet actually scans for
let key = ProjectivePoint::GENERATOR * Scalar::ONE;
let real_offset = Scalar::from(5u64);
let script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * real_offset)).unwrap();

// Attacker crafts bytes: same output, but claims offset = 7
let forged = ReceivedOutput { offset: Scalar::from(7u64), /* same TxOut/outpoint */ };
let bytes = forged.serialize();
let decoded = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted

// Output::key() now returns script_key - 7*G = key - 2*G, not `key`
// -> output attributed to a key that cannot spend it, no error raised
```

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

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
  }
```
