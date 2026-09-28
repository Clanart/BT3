### Title
`ReceivedOutput::read` accepts an arbitrary offset/script pairing, misrepresenting unspendable outputs as spendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report concerns critical information being displayed/accepted under a wrong identity (branch-name confusion). The Serai analog is `ReceivedOutput`, the object the Bitcoin wallet reports as "funds received and spendable." Its deserializer binds three claims together — an `offset` scalar, a `TxOut` (script + value), and an `OutPoint` — yet performs no consistency check between them, so untrusted bytes can assert that an output paying to script X is spendable via offset Y.

### Finding Description
`ReceivedOutput::read` reads each field independently: `Secp256k1::read_F` for the offset, then `TxOut::consensus_decode` and `OutPoint::consensus_decode` [1](#0-0) . It never verifies that `key + G*offset` actually derives `output.script_pubkey` — the very invariant `Scanner` establishes when it produces `ReceivedOutput`s legitimately via `scripts.get(&output.script_pubkey)` [2](#0-1) . Worse, the claimed `outpoint` need not even be an outpoint of a transaction containing that `TxOut`; nothing ties the three fields together.

A related confusion exists in `Output::key()` (processor side, illustrative of the intended invariant): the spend key is recovered as `xonly(script_pubkey) - G*offset` [3](#0-2) , so any offset that doesn't match the script silently yields the wrong spend key rather than an error.

### Impact Explanation
A `ReceivedOutput` is the wallet's representation of "received, spendable funds." By feeding crafted bytes to `ReceivedOutput::read` (an explicitly untrusted input path), an attacker can cause funds to be reported received that are not spendable: the declared value/script may be real on-chain, but the stored offset produces a signing key that does not correspond to the script_pubkey, so the threshold spend signs for a key that cannot satisfy the output's witness. Alternatively the outpoint can point at an output the attacker controls while the script_pubkey matches a scanner-registered offset, conflating attacker-owned funds with protocol-owned ones (value misattribution). This is the analog of the GitLab confusion: an object asserting a false binding between identity (spend key) and subject (the on-chain output).

### Likelihood Explanation
Reachable wherever `ReceivedOutput`s transit untrusted channels — stored scanner results relayed between components, DB records, or cross-network output reports — and consumed by callers that trust `offset()`/`output()`/`outpoint()` as a coherent triple, since `read` provides no validation hook [4](#0-3) . The attacker needs only to supply bytes; no validator status or key material is required.

### Recommendation
After decoding in `ReceivedOutput::read` (and/or in a `verify`/`new` constructor), recompute `p2tr_script_buf(base_key + G*offset)` and reject if it does not equal `output.script_pubkey`. The base key isn't stored, so either store it, or store the derived key and check `x_only(derived) == x_only(script_pubkey)` plus even parity, mirroring `Output::key`'s reconstruction [5](#0-4) .

### Proof of Concept
```
// Attacker crafts bytes for an output they sent to a victim's *registered*
// script_pubkey, but pairs it with offset = 1 (or any wrong scalar):
let mut buf = Vec::new();
buf.extend(Scalar::ONE.to_bytes().as_ref());           // wrong offset
buf.extend(serialize(&victim_paid_txout));            // real TxOut to scanner script
buf.extend(serialize(&real_outpoint));                // real outpoint
let o = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // accepted, no check
// o.value() reports real received funds; spending uses base + G*1,
// which does not equal the script's key -> output unspendable by that proof.
```
No code path rejects this object; the inconsistency only surfaces later as a failed spend.

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
