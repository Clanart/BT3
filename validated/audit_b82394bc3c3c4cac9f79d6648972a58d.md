Let me quickly check how the transaction machine handles per-input signing and offsets.### Title
`SignableTransaction::new` accepts the same UTXO twice, producing a double-spend transaction the multisig will still sign - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` takes a caller-supplied `Vec<ReceivedOutput>` and never checks for duplicate outpoints. A `ReceivedOutput` is deserializable from untrusted bytes via `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122`), so duplicate inputs are reachable from public input. This is the Serai-side analog of CVE-2021-40574's double-free: a resource (a UTXO) is consumed twice, once per list entry, and the value is double-counted while the same outpoint is spent twice in one transaction.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- `input_sat` sums `input.output.value` over every entry, with no deduplication, so a duplicated `ReceivedOutput` counts its value twice toward the funds check [1](#0-0) .
- `tx_ins` is built by mapping every input to a `TxIn` keyed by `input.outpoint`, so duplicates produce two `TxIn`s spending the identical outpoint [2](#0-1) .
- The sufficiency check `input_sat < payment_sat + needed_fee` then passes on phantom funds [3](#0-2) .
- `multisig` verifies each input independently (`p2tr_script_buf(offset.group_key()) != prevouts[i].script_pubkey`), which passes for the duplicate since both entries are identical, and spawns a FROST signing machine per input [4](#0-3) .
- `TransactionSignMachine::sign` signs each input under `Prevouts::All`, so both duplicate inputs receive valid Schnorr shares; `complete` attaches a valid witness to each [5](#0-4) .

The result is a fully "signed" transaction that is consensus-invalid: Bitcoin rejects any transaction whose input list contains the same outpoint twice. Nothing anywhere in `new`, `multisig`, or `scan_transaction` rejects duplicated inputs — `scan_transaction` itself emits one `ReceivedOutput` per matching `vout`, so identical `ReceivedOutput` values can only be distinguished by their `outpoint`, which `new` never compares [6](#0-5) .

### Impact Explanation
- Funds accounting is wrong: the duplicated UTXO's value is double-counted, so a transaction can be constructed whose payments exceed the real funds available. The change calculation at lines 224-235 may also compute a change amount based on phantom input value [7](#0-6) .
- The threshold multisig consumes a full FROST signing round (preprocess + share, i.e., real nonce consumption by every participant) to sign a transaction that can never confirm. The plan making the payment is stuck: the signatures are valid but the transaction is unbroadcastable, so payments are never made and the inputs must be recovered through a new plan.
- `fee()` reports `sum(prevouts) - sum(outputs)` computed over the inflated prevout sum, so fee/eventuality accounting around this transaction is also incorrect [8](#0-7) .

### Likelihood Explanation
`SignableTransaction::new` accepts an arbitrary `Vec<ReceivedOutput>` and `ReceivedOutput::read` accepts untrusted bytes [9](#0-8) . Any path that feeds deserialized or plan-constructed input lists into `new` can trigger this; a duplicate only requires the same `(offset, output, outpoint)` triple appearing twice. There is no deduplication anywhere between construction and broadcast. Severity is bounded to Medium because the tx is invalid rather than theft-enabling — funds are locked/wasted-signing-round, not stolen — and a cooperating scheduler that supplies the inputs must be the one to introduce the duplication.

### Recommendation
In `SignableTransaction::new`, reject duplicate inputs before use:

```rust
let mut seen = HashSet::with_capacity(inputs.len());
for input in &inputs {
  if !seen.insert(input.outpoint) {
    Err(TransactionError::DuplicateInput)?;
  }
}
```

This should be checked on the `OutPoint`, not the whole `ReceivedOutput`, since the outpoint is the resource being consumed.

### Proof of Concept
```rust
// One real scanned output
let output: ReceivedOutput = scanner.scan_transaction(&funding_tx).swap_remove(0);

// Pass it twice — no error is raised
let tx = SignableTransaction::new(
  vec![output.clone(), output],          // same outpoint twice
  &[(destination_script, output_value)], // affordable only via double-counting
  Some(change_script),
  None,
  FEE,
).unwrap();

// input_sat counted the UTXO twice; the tx has two TxIns for one outpoint.
// multisig(keys) succeeds and the threshold group signs it, but Bitcoin Core
// rejects it as a double-spend (bad-txns-inputs-duplicate).
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
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

**File:** networks/bitcoin/src/wallet/send.rs (L177-185)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
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
    }
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
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
