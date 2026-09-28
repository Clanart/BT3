### Title
`ReceivedOutput::read` mass-assigns the spending `offset` from untrusted bytes without binding it to the output's `script_pubkey`, producing outputs that scan as received but are unspendable — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` is the structure Serai uses to remember a detected payment together with the scalar offset needed to spend it. The spend-critical field — `offset` — is not derivable from the output itself; it is only correct when it satisfies `script_pubkey == p2tr(key + offset·G)`. The `scan_transaction` path enforces this binding implicitly by looking the offset up in `self.scripts` keyed by the script [1](#0-0) , but `ReceivedOutput::read` accepts `offset` verbatim from the wire with no consistency check against `output.script_pubkey` [2](#0-1) . This is the same defect class as the Flowise mass-assignment bug: an authorization/ownership-critical field supplied in attacker-controlled data is trusted instead of being re-derived or validated against the object it governs.

### Finding Description
`ReceivedOutput::read` deserializes three fields independently: `offset` via `Secp256k1::read_F`, then `output` (`TxOut`) and `outpoint` via consensus decoding [2](#0-1) . There is no field storing the base key and no verification that `p2tr_script_buf(key + GENERATOR·offset)` equals `output.script_pubkey` — the only place that relation exists is inside `Scanner::register_offset`/`scan_transaction` [3](#0-2) .

Downstream, `SignableTransaction`/the multisig path signs a Taproot key-path spend using `keys.offset(output.offset())` semantics (the offset is applied to `ThresholdKeys` via `ThresholdKeys::offset`, which shifts the group key by `offset·G` [4](#0-3)  and [5](#0-4) ). A BIP-341 key-path signature for a prevout only verifies if the prevout's committed x-only key equals `group_key + offset·G` (post-tweak). If an attacker feeds the wallet/decoder a `ReceivedOutput` whose `offset` does not match the real `script_pubkey` — e.g., an output actually paying to `key + a·G` paired with claimed offset `b ≠ a`, or an output paying to an unrelated key with offset `0` — Serai reports the output as received, then produces a signature under the wrong tweaked key, making the spend invalid on-chain.

### Impact Explanation
An unprivileged party who can supply or corrupt serialized `ReceivedOutput` bytes (the documented sink for untrusted input) causes funds to be reported as received that are not spendable: the processor will attempt a spend, the resulting signature fails BIP-341 verification, and the true funds (if any) are stranded or the entry is phantom. Because `offset` is the *only* datum connecting the output to the spending key, this is a direct integrity failure of the payment-tracking record — the analog of Flowise's `workspaceId` being accepted from request input to claim another tenant's evaluation.

### Likelihood Explanation
Requires an attacker to control or tamper with the bytes fed to `ReceivedOutput::read` (e.g., untrusted serialization of scan results relayed between components). It does not require threshold collusion, a malicious validator, or leaked keys — only malformed input to a public `read` API. Impact is bounded to loss of spendability/availability of the affected output (Medium), since an attacker cannot pick an offset that makes a signature verify for a key whose discrete log they don't know — they can only break the binding, not forge spends of Serai outputs to themselves.

### Recommendation
Include the base `key` (or the scanner's key commitment) in `ReceivedOutput`, and in `read` (or a new `verify`/`authenticate` step) recompute `p2tr_script_buf(key + GENERATOR·offset)` and reject the record unless it equals `output.script_pubkey`. This makes the serialized representation self-authenticating: the offset becomes bound to the output it claims to spend, mirroring the fix of deriving ownership fields server-side rather than accepting them from client input.

### Proof of Concept
1. Scanner legitimately registers offset `a` and observes output `O` paying to `p2tr(K + a·G)` [6](#0-5) .
2. Attacker intercepts/serializes `ReceivedOutput { offset: a, output: O, outpoint: P }` and flips the field to `offset: b` (`b ≠ a`), or crafts `ReceivedOutput { offset: 0, output: TxOut paying to attacker script, outpoint: P }` pointing at an output never owned by Serai.
3. `ReceivedOutput::read` accepts the record without any consistency check [2](#0-1) .
4. The wallet builds a `SignableTransaction` spending `P`; signers sign under key `K + b·G` (or `K`), which does not match `O`'s committed key, so the BIP-341 signature is invalid and the transaction cannot be broadcast — the output was reported received yet is unspendable. In the phantom variant, the node forever counts attacker-controlled sats it can never move.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-213)
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
```

**File:** crypto/dkg/src/lib.rs (L414-417)
```rust
  pub fn offset(mut self, offset: C::F) -> ThresholdKeys<C> {
    self.offset += offset;
    self
  }
```

**File:** crypto/dkg/src/lib.rs (L445-447)
```rust
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
```
