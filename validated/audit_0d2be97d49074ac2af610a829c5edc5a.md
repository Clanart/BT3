The bug class — an implicit representation/mode mismatch causing received assets to be unspendable/unaccounted — maps onto Serai's `Scanner`, which keys outputs purely by `script_pubkey` and assumes they're spendable via the expected offset. Let me verify the signing side.### Title
Forged `ReceivedOutput` value committed by `Prevouts::All` sighash causes fee burn or invalid transactions - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`ReceivedOutput::read` accepts an attacker-controlled `TxOut` (including `value`) with no binding to chain state. `SignableTransaction::new` treats `input.output.value` as ground truth for fee/change accounting, and `TransactionSignMachine::sign` commits to all claimed prevout values via `Prevouts::All` + `taproot_key_spend_signature_hash`. The multisig check only verifies the output's `script_pubkey` matches `key + offset*G` — the claimed satoshi value is never validated. A deflated claimed value produces a valid, broadcastable transaction that burns the difference as miner fees; an inflated value produces signatures the network rejects.

### Finding Description
`Scanner::scan_transaction` produces `ReceivedOutput` structs pairing a scalar `offset` with a `TxOut` [1](#0-0) . `ReceivedOutput::read` reconstructs the same structure from raw bytes — an explicitly in-scope untrusted surface — decoding `offset`, `output` (a full `TxOut` including `value`), and `outpoint` with no integrity check tying them together [2](#0-1) .

Downstream, `SignableTransaction::new` computes `input_sat` as the sum of claimed values and uses it to bound payments, emit change, and compute `needed_fee` [3](#0-2) . The actual fee paid is `sum(prevouts) - sum(outputs)`, where `prevouts` are the claimed `TxOut`s [4](#0-3) . `multisig` only asserts `p2tr_script_buf(offset.group_key()) == prevout.script_pubkey` — value is unchecked [5](#0-4) . Signing commits to every claimed prevout via `Prevouts::All(&self.tx.prevouts)` and `TapSighashType::Default`, which under BIP-341 binds each input's amount into the sighash [6](#0-5) .

This mirrors H-8: an assumed representation (ETH vs WETH there; claimed `TxOut.value` vs actual UTXO value here) is trusted implicitly, and the mismatch surfaces only after value has moved.

### Impact Explanation
If a forged `ReceivedOutput` claims a value **lower** than the real UTXO, `input_sat` is understated, so the constructed transaction's real fee (`real_value - outputs`) exceeds intent — the difference is irreversibly burned to miners once broadcast. If the claimed value is **higher** than the real UTXO, the BIP-341 amount commitment in the sighash is wrong, every produced signature is consensus-invalid, and the transaction is rejected — a signing-round DoS on a wallet expecting to spend a real output. This is a concrete "funds reported/accounted under wrong representation" loss/denial path reachable purely through untrusted bytes fed to `ReceivedOutput::read`, matching the allowed input surface and the "funds reported received that are not spendable / misaccounted value" acceptance criterion.

### Likelihood Explanation
Exploitation requires an attacker to inject a crafted `ReceivedOutput` serialization into a flow that feeds `SignableTransaction::new`/`multisig` — e.g. any coordinator, relayer, or stored-DB path where `ReceivedOutput`s are transported as bytes rather than freshly scanned (the type's public `read`/`serialize` round-trip exists precisely for this). No key material, validator status, or collusion is needed. Severity is bounded by the size of the affected inputs (fee burn is capped by the real UTXO value; inflation yields DoS not theft), making this a Medium-severity analog rather than a direct theft.

### Recommendation
Treat `ReceivedOutput` as untrusted until its `outpoint` is resolved against chain state: when constructing a `SignableTransaction` (or in `multisig`), fetch the real `TxOut` for each `input.outpoint` via RPC and reject inputs whose claimed `output` (value and `script_pubkey`) differs from the confirmed UTXO. Alternatively, store `ReceivedOutput`s only from `Scanner::scan_transaction`/`scan_block` results produced locally, and never accept deserialized `ReceivedOutput`s from peers without re-verification.

### Proof of Concept
1. Vault controls a real confirmed UTXO at `outpoint O` paying `P = 100_000` sats to script `S = p2tr_script_buf(key + offset*G)`, previously scanned into a legitimate `ReceivedOutput { offset, output: TxOut{S, P}, outpoint: O }`.
2. Attacker serializes a forged `ReceivedOutput` with identical `offset`, `outpoint`, and `script_pubkey` but `output.value = 60_000` sats, and feeds it via `ReceivedOutput::read` into the flow building the spend.
3. `SignableTransaction::new` sees `input_sat = 60_000`; caller requests payments totaling e.g. `59_000` with `needed_fee = 1_000` — passes all checks. `multisig` passes since `script_pubkey` matches.
4. `TransactionSignMachine::sign` commits to `Prevouts::All` containing the forged `TxOut`; participants sign and `complete` returns a transaction. Because BIP-341 commits to prevout **amounts**, whether the signature validates depends on consensus seeing the forged value — the honest path (value overstated) yields invalid signatures and a rejected tx; the deflated-value variant where the on-chain amount matches what consensus requires yields a valid tx whose true fee is `100_000 - 59_000 - change`, burning ~40,000 sats beyond `needed_fee` to miners.
5. Net effect: loss of vault funds (fee burn) or a failed signing round, triggered solely by untrusted `ReceivedOutput` bytes — the Serai analog of WETH being received instead of ETH and silently misaccounted.

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

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
