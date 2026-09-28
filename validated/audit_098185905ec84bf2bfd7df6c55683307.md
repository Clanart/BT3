### Title
`SignableTransaction::new` accepts duplicate inputs, producing a fully-signed transaction that spends the same UTXO twice and is consensus-invalid - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to filling an already-filled order (missing `require(remainingMakerAmount > 0)`), `SignableTransaction::new` never checks that the provided `ReceivedOutput`s are distinct. Each input's value is summed into `input_sat` and each outpoint is mapped into a `TxIn` without any uniqueness check [1](#0-0) . A duplicate outpoint therefore double-counts funds and yields a transaction with two identical inputs, which Bitcoin consensus rejects (duplicate prevouts fail `CheckTransaction`), yet the code proceeds to threshold-sign it via `Prevouts::All` and returns it from `complete` [2](#0-1) .

### Finding Description
`SignableTransaction::new` takes `inputs: Vec<ReceivedOutput>` and:
1. Sums `input.output.value` into `input_sat` (line 175), so a duplicated output inflates the apparent balance and defeats the `NotEnoughFunds` guard at lines 215-221.
2. Builds `tx_ins` and `prevouts` directly from the vector (lines 177-185, 253) with no check that two entries share the same `outpoint`.

Each input then gets its own FROST machine bound to the same offset key [3](#0-2) , and `TransactionSignMachine::sign` produces valid per-input BIP-340 signature shares over `Prevouts::All`, which contains the duplicated prevout. `TransactionSignatureMachine::complete` assembles the final transaction [4](#0-3) . The result is an honestly-signed transaction spending one UTXO twice — permanently unbroadcastable.

The untrusted-bytes entry point exists: `ReceivedOutput::read` parses attacker-supplied bytes into `ReceivedOutput` [5](#0-4) , and the constructed `ReceivedOutput` (with arbitrary offset/outpoint) is exactly what `SignableTransaction::new` consumes.

### Impact Explanation
The threshold set produces a consensus-invalid transaction: Bitcoin's `CheckTransaction` rejects any tx with duplicate `previous_output`s, so the signed transaction can never confirm. The `NotEnoughFunds` check is bypassed (the same satoshis counted twice), meaning the payment/change/fee math is computed against phantom balance. In a batch flow this consumes a full signing round and locks the referenced output into a dead transaction, requiring re-planning; repeated injection produces a liveness failure for payments. This is the direct analog of the report: an operation on an already-consumed resource is accepted because no "already spent / already included" check exists.

### Likelihood Explanation
An unprivileged party who can feed bytes through `ReceivedOutput::read` into transaction construction (the only place `ReceivedOutput` is created besides the honest `Scanner::scan_transaction`, which itself emits whatever the chain contains [6](#0-5) ) can submit the same output twice. No sophisticated capability is needed — just duplicating a valid serialized output. The resulting failure is deterministic: the signed transaction is always invalid.

### Recommendation
In `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs), reject duplicate outpoints before building `tx_ins`, e.g. insert each `input.outpoint` into a `HashSet` and return a new `TransactionError::DuplicateInput` variant if insertion fails. This mirrors the recommended `require(remainingMakerAmount > 0)` guard: refuse to operate on a resource already consumed in the same operation.

### Proof of Concept
```rust
// networks/bitcoin (std feature)
let scanner = Scanner::new(tweaked_group_key).unwrap();
// tx pays `key` once; `out` is the single resulting ReceivedOutput
let outputs = scanner.scan_transaction(&funding_tx);
let out = outputs[0].clone();

let payment = (script_pubkey, out.value() * 2 - some_fee); // ~2x the real balance
let stx = SignableTransaction::new(
    vec![out.clone(), out],          // same outpoint twice
    &[(payment_script, payment_amt)], // passes NotEnoughFunds via double-counting
    None, None, fee_per_vbyte,
).unwrap(); // No error is raised today

// Each participant runs:
let machine = stx.clone().multisig(&keys).unwrap();
let (machine, preprocess) = machine.preprocess(&mut OsRng);
let (machine, share) = machine.sign(preprocesses_from_peers, &[]).unwrap();
let signed_tx = machine.complete(shares_from_peers).unwrap();

assert_eq!(
    signed_tx.input[0].previous_output,
    signed_tx.input[1].previous_output
); // same UTXO consumed twice
// `signed_tx` is rejected by Bitcoin's CheckTransaction (duplicate inputs),
// despite carrying valid BIP-340 signatures from the threshold set.
```

The missing check is at `networks/bitcoin/src/wallet/send.rs:175-185` — there is no equivalent of `require(remainingMakerAmount > 0)` ensuring an input hasn't already been consumed.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L413-428)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
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
