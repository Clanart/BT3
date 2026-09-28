### Title
Attacker-deserialized `ReceivedOutput` inflates input balance and yields signed transactions spending nonexistent outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes an accounting bug: `getTVL()` counts assets that are queued for withdrawal, overstating the spendable balance and breaking downstream share math. The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs:122-134`, which deserializes a purported received-and-spendable output from raw bytes with no binding back to the `Scanner` that is supposed to produce it. An unprivileged party feeding bytes to `ReceivedOutput::read` can mint a `ReceivedOutput` claiming an arbitrary amount under any of the multisig's (public) P2TR scripts, inflating the input total `SignableTransaction::new` accounts for and causing a threshold-signed transaction that spends a nonexistent output — funds reported received that are not spendable.

### Finding Description
`Scanner::scan_transaction` is the only honest constructor of `ReceivedOutput`, and it guarantees an invariant: the output's `script_pubkey` was registered via `Scanner::new`/`register_offset`, so `offset` recovers a key that can spend it, and the `outpoint`/`value` correspond to a real on-chain UTXO ( [1](#0-0) ).

`ReceivedOutput::read` accepts a scalar offset, a `TxOut`, and an `OutPoint` from the byte stream with no validation whatsoever ( [2](#0-1) ). Downstream, `SignableTransaction::new` sums `input.output.value` into `input_sat` and treats it as available balance ( [3](#0-2) ), and `SignableTransaction::multisig` only verifies that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` ( [4](#0-3) ) — i.e., it checks the script↔offset binding but never that the claimed outpoint exists or that the value is real.

The multisig's P2TR script pubkeys are public on-chain data. An attacker therefore serializes `ReceivedOutput { offset: Scalar::ZERO, output: TxOut { value: huge, script_pubkey: p2tr_script_buf(group_key) }, outpoint: <arbitrary> }`, and it passes both `read` and the `multisig()` check. The phantom value is counted toward `input_sat`, suppressing the `NotEnoughFunds` check ( [5](#0-4) ), and the FROST threshold signature is produced over a transaction spending a fake prevout (`Prevouts::All` commits to the fabricated `prevouts`, [6](#0-5) ).

### Impact Explanation
This mirrors the source bug's shape: a balance figure is computed from entries that were never validated as spendable, and that figure is consumed by value-accounting logic (fee/change math, `NotEnoughFunds`). Concrete effects:
- The input-balance ledger is overstated by an arbitrary attacker-chosen amount; change, fee-sufficiency, and payment coverage are computed against phantom funds.
- The signing machine produces a valid FROST signature over a transaction the Bitcoin network will reject (the input outpoint doesn't exist or isn't the claimed one), so funds the integrator believes were received are not spendable — the exact "funds reported received that are not spendable" impact.
- One attacker-injected `ReceivedOutput` poisons every real input aggregated with it in the same transaction, stalling the multisig's spend pipeline.

### Likelihood Explanation
Reachability requires an integrator that accepts serialized `ReceivedOutput`s from an untrusted peer (the type exposes `read`/`serialize`/`write` precisely for transport, and `ReceivedOutput::read` is explicitly an untrusted-byte sink). No key material, collusion, or validator misbehavior is needed — only crafted bytes. The group key and all registered scripts are public, so constructing a passing `script_pubkey`/`offset` pair is trivial. Where the serialized form is only read from local, self-written storage (e.g., a scanner DB), the attack surface is absent, which is why this is Medium rather than High.

### Recommendation
Bind deserialization to the scanning invariant. Options:
- Make `ReceivedOutput::read` take the `Scanner` (or the group key) and reject any output whose `script_pubkey` is not in `scanner.scripts`, re-deriving `offset` from the map instead of trusting the serialized scalar — this also removes the duplicated `offset` field entirely, since it is already recoverable from `script_pubkey`.
- Alternatively, split the type: an unvalidated `ClaimedOutput` produced by `read`, and `ReceivedOutput` only obtainable via `Scanner::scan_transaction`/`scan_block` or a `verify_against(scanner)` upgrade step.
- At minimum, have `SignableTransaction::multisig` document that `prevouts` must originate from confirmed chain data, since it cannot detect fabricated outpoints.

### Proof of Concept
```rust
// Attacker-side: craft a phantom deposit under the real multisig script.
let group_key: ProjectivePoint = /* public group key */;
let script = p2tr_script_buf(group_key).unwrap();

let mut bytes = Vec::new();
// offset = 0 -> keys.offset(0).group_key() == group_key, so the multisig() check passes
bytes.extend_from_slice(&Scalar::ZERO.to_bytes());
bytes.extend_from_slice(&bitcoin::consensus::encode::serialize(&TxOut {
  value: Amount::from_sat(1_000_000_000),          // phantom balance
  script_pubkey: script,
}));
bytes.extend_from_slice(&bitcoin::consensus::encode::serialize(&OutPoint {
  txid: Txid::all_zeros(), vout: 0,                 // nonexistent outpoint
}));

let fake = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();      // accepted, no checks

// SignableTransaction::new counts `fake.value()` toward `input_sat`,
// suppressing NotEnoughFunds, and `multisig()` accepts it because
// p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey.
// The threshold then signs a tx spending a nonexistent prevout, which
// the Bitcoin network rejects: the "received" funds are unspendable.
```
Contrast with `scan_transaction` ( [1](#0-0) ), which only emits outputs proven to exist in a parsed transaction with a registered script — an invariant `read` does not preserve.

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-175)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L276-279)
```rust
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L375-390)
```rust
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
