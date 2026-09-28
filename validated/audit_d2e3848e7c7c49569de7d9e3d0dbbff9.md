### Title
`SignableTransaction::multisig` binds the spent output's `script_pubkey` but never its value, so a forged `ReceivedOutput` causes signing of a transaction committing to a fabricated input value - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The OpenClaw bug is a TOCTOU pattern: a resource is validated, then a *separately obtained* object is used. The bitcoin-serai analog lives in the gap between `Scanner::scan_transaction` / `ReceivedOutput::read` and `SignableTransaction::multisig`. A `ReceivedOutput` couples three independently controlled fields — `offset`, the full `TxOut` (`value` + `script_pubkey`), and the `outpoint`. `multisig()` checks only that the recomputed `script_pubkey` equals `p2tr_script_buf(offset.group_key())` (`send.rs:277`); the `value` inside `prevouts[i]` is never authenticated against the real UTXO at `outpoint`. The sighash then commits `Prevouts::All(&self.tx.prevouts)` (`send.rs:375-386`), i.e., it signs over the unverified values. A party that feeds attacker-crafted bytes through `ReceivedOutput::read` (`wallet/mod.rs:122-134`) can therefore cause the threshold to sign a transaction whose committed input values differ from the real outputs being spent. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
- `ReceivedOutput::read` deserializes `offset` (`Secp256k1::read_F`), then `TxOut::consensus_decode` and `OutPoint::consensus_decode` (`wallet/mod.rs:122-134`). None of these are cross-checked; a `ReceivedOutput` is fully attacker-constructible.
- `SignableTransaction::new` (`send.rs:150-256`) computes `input_sat` from the supplied `input.output.value` (`send.rs:175`), sizes payments/change against it (`send.rs:215`, `send.rs:228-230`), and stores the attacker-controlled `TxOut`s verbatim as `prevouts` (`send.rs:253`).
- `multisig()` (`send.rs:273-285`) performs the only validation: `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey`. `value` is outside the check.
- `TransactionSignMachine::sign` (`send.rs:355-398`) builds `Prevouts::All(&self.tx.prevouts)` and signs `taproot_key_spend_signature_hash(i, ...)` per input — committing to the fabricated value.

This is the same check-then-use shape as the advisory: the check happens on one field (`script_pubkey`), while the used object (`value`, and implicitly the `outpoint`→UTXO binding) is a distinct, unvalidated quantity.

### Impact Explanation
Two concrete outcomes reachable with only crafted `ReceivedOutput` bytes passed to `read` → `SignableTransaction::new` → `multisig` → `sign`:

- **Understated value** (`value < actual UTXO`): the produced transaction is still valid and broadcastable. The difference between the real input amount and the declared outputs becomes miner fee. The threshold signs an unintended spend that silently burns `actual - declared` of vault funds as excess fee — signing of an unintended message / unspendable-funds loss.
- **Overstated value** (`value > actual`): `input_sat` accounting admits payments/change the real inputs can't cover; the sighash commits to a nonexistent amount, producing a threshold-signed transaction that can never confirm — a signed plan whose funds are reported payable but are not spendable.

The `outpoint` itself is likewise unchecked (any outpoint with a matching `script_pubkey` is accepted), enabling misattribution across outputs sharing the script.

### Likelihood Explanation
Requires an attacker to control the serialized `ReceivedOutput` consumed by `ReceivedOutput::read` — e.g., a counterparty asserting "I paid you N sats at this outpoint" through any channel that feeds the `read`/`serialize` round-trip rather than values produced by `Scanner::scan_transaction`. It does not require a malicious validator, leaked key, or forged proof — only untrusted bytes, matching the reachable-inputs constraint. It cannot be triggered through `Scanner::scan_transaction` output alone, since that path copies the real `TxOut`. Likelihood is therefore moderate: it depends on an integrator (or protocol layer) trusting a peer-supplied `ReceivedOutput` rather than self-scanning.

### Recommendation
- In `SignableTransaction::new` or `multisig`, treat `ReceivedOutput` as unauthenticated: re-derive the spendable condition from `(outpoint, offset)` and verify the committed `TxOut` against the actual UTXO (or require callers to pass scanner-produced outputs only, documented as a MUST).
- At minimum, bind `value` alongside `script_pubkey` at the check site and reject `ReceivedOutput`s whose `offset`/`script_pubkey`/`value` combination cannot be reproduced, so the checked object and the signed object are identical — closing the TOCTOU gap.

### Proof of Concept
```rust
// From networks/bitcoin/src/wallet/mod.rs:122 — attacker-controlled bytes
let mut bytes = received.serialize(); // honest output: value = V
// Rewrite the TxOut amount field to V' = V - delta (or V + delta)
// offset still matches script_pubkey, so send.rs:277 check passes
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// send.rs:150 — builds prevouts from forged.value; fee accounting uses forged value
let tx = SignableTransaction::new(vec![forged], &payments, change, None, FEE).unwrap();
// send.rs:273 — multisig() accepts: script_pubkey check still holds
let machine = tx.clone().multisig(&keys[&i]).unwrap();
// send.rs:375 — sighash commits Prevouts::All with the forged value;
// the threshold co-signs a tx that either burns (V - V') as fee (V' < V)
// or is permanently invalid (V' > V), despite signers approving an honest plan.
```

Uncertain aspects: whether any in-repo caller actually feeds remote bytes into `ReceivedOutput::read` (the processor's `Output::read` path at `processor/src/networks/bitcoin.rs:156` is out of scope but is the plausible consumer), and whether Bitcoin consensus details of `Prevouts::All` vs. per-input committed values alter the worst-case framing. The core gap — checked field ⊊ signed field in `multisig()` — is directly evident in `send.rs:273-285`.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
