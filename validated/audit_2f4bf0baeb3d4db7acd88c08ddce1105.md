### Title
SignableTransaction trusts the self-reported `ReceivedOutput` value instead of the actual UTXO amount, producing invalid signatures or silently burned funds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The audit finding's bug class is: an internally tracked amount (`pool.amount`) is used in place of the true on-chain amount, and drift between the two causes either transaction failure or orphaned funds. `SignableTransaction` in `networks/bitcoin/src/wallet/send.rs` is the direct analog: it builds and signs a Bitcoin transaction using `input.output.value` carried inside each `ReceivedOutput`, a value that is never validated against the actual UTXO on chain. `ReceivedOutput` is just a deserialization of attacker-influenceable bytes via `ReceivedOutput::read` [1](#0-0) , and nothing cross-checks the claimed `TxOut` against the outpoint's real contents.

### Finding Description
`SignableTransaction::new` sums `input.output.value.to_sat()` to compute `input_sat`, uses that for the `NotEnoughFunds` check, and derives the change amount from it [2](#0-1) [3](#0-2) . The claimed prevouts are then committed into the Taproot sighash via `Prevouts::All(&self.tx.prevouts)` [4](#0-3) . So the "tracker" here is the `TxOut` stored inside `ReceivedOutput`, which may diverge from the real UTXO (e.g., stale/mismatched scan data, a `ReceivedOutput` reconstructed via `read`, or any upstream bookkeeping error of the same kind as `pool.amount`). The code only verifies that the *script pubkey* matches the offset key in `multisig` [5](#0-4)  — it never verifies the amount, and the whole FROST threshold will cooperatively sign whatever value the tracker claims.

### Impact Explanation
Two symmetric failure modes, mirroring the original report:

1. **Tracked value > actual UTXO value.** Change is computed too large (`input_sat - payment_sat - fee_with_change`), and the BIP-341 sighash commits to the inflated prevout amount. The resulting signatures are invalid on chain, so the signed transaction can never be broadcast — the threshold wastes a signing session and the spend outright fails, analogous to the `MsgBeginRedelegate` abort.
2. **Tracked value < actual UTXO value.** The signatures are still valid (they commit to the understated amount, which matches what was signed), but the transaction's effective fee becomes `actual_inputs - declared_outputs`, silently burning the difference as miner fee — the "partial redelegation / orphaned funds" analog, where real satoshis are lost from the multisig's perspective while the bookkeeping believes everything was accounted for.

### Likelihood Explanation
Any path where the `TxOut` inside a `ReceivedOutput` does not come directly from a freshly scanned, confirmed block produces drift: reserialization through `ReceivedOutput::read`/`write`, planner-side output stores, or reorg/stale-scan edge cases. The scheduler/planner layer passes these outputs into `SignableTransaction::new` without re-fetching the prevout from the node. Drift requires only a bookkeeping inconsistency, not adversarial consensus — comparable to the report's Medium likelihood.

### Recommendation
Before signing, verify each input's claimed amount against the chain: fetch the prevout via RPC for each `OutPoint` (or attach a merkle/block proof) inside `SignableTransaction::new` or `multisig`, and reject construction if `output.value`/`script_pubkey` diverge from the real UTXO. At minimum, re-query prevouts at planning time rather than trusting the serialized `ReceivedOutput` — the same fix as querying the real delegation instead of `pool.amount`.

### Proof of Concept
1. Construct a `ReceivedOutput` whose `outpoint` refers to a real on-chain UTXO worth 50,000 sats, but whose embedded `TxOut.value` claims 40,000 sats (e.g., by mutating bytes and re-reading via `ReceivedOutput::read`).
2. Build `SignableTransaction::new(vec![output], payments, Some(change), None, fee)` with payments of 30,000 sats. The change output is computed as ~10,000 − fee.
3. Run `multisig`/`preprocess`/`sign`/`complete` with valid `ThresholdKeys`. The script-pubkey check passes; signatures verify against the committed prevouts.
4. Broadcast: the transaction is valid (sighash committed to the claimed 40,000), but the real input supplies 50,000 — the extra 10,000 sats are paid as an unintended fee. Conversely, claiming 60,000 produces signatures no node will accept, permanently failing the spend.

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-175)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-234)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-281)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-386)
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
```
