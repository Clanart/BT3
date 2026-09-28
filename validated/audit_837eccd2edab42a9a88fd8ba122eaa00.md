### Title
`ReceivedOutput::read` and `SignableTransaction::new` trust attacker-controlled `TxOut` value/outpoint, causing signatures over forged prevout data - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog of "unchecked parameter executes unintended operations" is that a `ReceivedOutput` deserializes its `offset`, full `TxOut` (script_pubkey **and value**), and `OutPoint` entirely from untrusted bytes with no consistency check, and `SignableTransaction::new`/`TransactionSignMachine::sign` then commit to those values in a Taproot sighash while only `multisig()` validates the script_pubkey against `offset`.

### Finding Description
`ReceivedOutput::read` reads `offset`, `output` (a consensus-decoded `TxOut`), and `outpoint` from the reader and returns them unvalidated — there is no check that the `TxOut` actually matches the `OutPoint`, nor that `offset` corresponds to `script_pubkey` [1](#0-0) . `SignableTransaction::new` then uses the claimed `input.output.value` as `input_sat` for the funds sufficiency check and stores each claimed `TxOut` into `prevouts` [2](#0-1) [3](#0-2) . The only integrity check performed, in `multisig()`, compares `p2tr_script_buf(offset.group_key())` to `prevouts[i].script_pubkey` — it never verifies the claimed `value` or that `outpoint` exists [4](#0-3) . Finally, `sign()` computes each input's sighash with `Prevouts::All(&self.tx.prevouts)`, meaning the threshold group signs a digest committing to the attacker-supplied amounts and script_pubkeys [5](#0-4) .

### Impact Explanation
An unprivileged party who supplies or tampers with serialized `ReceivedOutput` bytes can cause the multisig to sign a transaction built on forged prevout data:

- **Inflated `value`**: the `NotEnoughFunds` check is bypassed and `needed_fee`/change are computed against phantom input value, while the resulting sighash commits to amounts that don't match the chain — the signed transaction is consensus-invalid, burning the spend attempt and potentially freezing the UTXO-dependent workflow.
- **Forged `outpoint`/`script_pubkey` pairing**: if the attacker keeps `script_pubkey` consistent with a registered `offset` (e.g., by reusing a real observed output's script) but points `outpoint` at a nonexistent or differently-valued UTXO, the group produces a signature for a spend that cannot confirm.
- The group is thereby induced to sign a sighash over message bytes (prevout commitments) it never intended — "concrete signing of an unintended message" via unchecked input fields, mirroring the CVE's pattern of an unvalidated parameter driving unintended behavior.

### Likelihood Explanation
Reachable by any party able to feed bytes to `ReceivedOutput::read` or supply `ReceivedOutput` values into transaction construction; no key share, validator status, or collusion is required. The cryptographic checks (FROST share verification, script_pubkey-vs-offset match) all still pass, so the attack is only limited by whether an integration deserializes untrusted `ReceivedOutput`s. Impact is bounded to invalid/fee-miscalculated transactions rather than key recovery, so Medium.

### Recommendation
- In `ReceivedOutput::read`, or in `SignableTransaction::new`/`multisig()`, verify the `TxOut` against the referenced `OutPoint` (requires a chain lookup) or, at minimum, document/enforce that `ReceivedOutput`s must originate from `Scanner::scan_transaction` and treat any other source as untrusted.
- Recompute the expected `script_pubkey` from `offset` inside `SignableTransaction::new` rather than in `multisig()` only, and reject mismatches before any signing machine is created.

### Proof of Concept
```rust
// Conceptual: attacker serializes a forged ReceivedOutput where `output.value`
// is inflated (e.g. 21M BTC) but `script_pubkey` is a real script matching `offset`,
// and `outpoint` names any outpoint. ReceivedOutput::read accepts it;
// SignableTransaction::new passes the funds check, and TransactionSignMachine::sign
// has the group sign taproot_key_spend_signature_hash over Prevouts::All
// containing the forged TxOut. The resulting signed TX is invalid on-chain
// (prevout amount mismatch), while all signature shares verify individually.
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

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L253-255)
```rust
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

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
