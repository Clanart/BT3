### Title
Inflated `ReceivedOutput` values produce unusable Bitcoin spends - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an independently supplied `TxOut` value and `OutPoint`, while `SignableTransaction::new` uses the claimed value as spendable input capacity. [1](#0-0) [2](#0-1)  Signing only verifies that the claimed script corresponds to the threshold key; it does not verify that the claimed amount is the amount actually held by the referenced UTXO. [3](#0-2)  Consequently, a forged record can make the wallet believe it has sufficient funds and cause it to produce a transaction whose Taproot signatures commit to a nonexistent prevout amount. [4](#0-3) 

### Finding Description
The scanner normally creates a `ReceivedOutput` from an observed transaction output, including its actual `TxOut` and outpoint. [5](#0-4)  The deserialization API instead accepts all three fields—offset, `TxOut`, and `OutPoint`—from untrusted bytes without authenticating their relationship to an on-chain UTXO. [1](#0-0) 

An attacker can reference a real, low-value UTXO paying the multisig, but pair its `OutPoint` with a forged `TxOut` containing the same script and a much larger amount. `SignableTransaction::new` sums the forged `input.output.value` fields and accepts payments based on that fictitious capacity. [6](#0-5)  The later `Prevouts::All` sighash commits to the fabricated prevout values, so the resulting signatures will not validate against the actual UTXO set. [4](#0-3) 

### Impact Explanation
Funds represented by a malicious `ReceivedOutput` are reported as available but are not actually spendable at the claimed amount. Transaction construction succeeds and threshold participants sign an invalid transaction rather than rejecting the input as unavailable. [7](#0-6) [8](#0-7)  This can stall payment execution and consume a threshold-signing round while the true low-value UTXO remains unspent. [9](#0-8) 

### Likelihood Explanation
An attacker first needs the wallet to process attacker-supplied `ReceivedOutput` bytes, which is the reachable boundary identified for this API. [1](#0-0)  The attacker can create the referenced low-value output by sending a normal Bitcoin transaction to the multisig’s P2TR script, then submit a forged record using that real outpoint but an inflated value. [5](#0-4)  The forged script must still correspond to the supplied offset because `multisig` rejects records whose claimed script does not match the offset-adjusted group key. [3](#0-2) 

### Recommendation
Treat deserialized `ReceivedOutput` values as unauthenticated claims and resolve each `OutPoint` against the Bitcoin UTXO set before calculating available funds or signing. [1](#0-0)  Replace the supplied `TxOut` with the node-verified prevout, or reject the input if the claimed value or script differs from the chain state. [6](#0-5)  Additionally, `SignableTransaction::new` should expose an explicit verified-input constructor so callers cannot accidentally build funding decisions from arbitrary serialized records. [10](#0-9) 

### Proof of Concept
1. Send 1,000 sats to the wallet’s P2TR script, producing real outpoint `O`.
2. Serialize a malicious record containing offset `0`, a `TxOut` with the same script but value `100_000_000`, and `O`.
3. Pass those bytes to `ReceivedOutput::read`.
4. Construct a `SignableTransaction` paying substantially more than 1,000 sats plus fees.
5. The input-value check passes because it uses the fabricated 100,000,000-sat `TxOut`. [6](#0-5) 
6. Threshold signing succeeds because `multisig` checks only the claimed script against the offset-adjusted key. [3](#0-2) 
7. The transaction is rejected by Bitcoin validation because each BIP-341 sighash committed to the fabricated prevout amount through `Prevouts::All`. [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-133)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-187)
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

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-232)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
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

**File:** networks/bitcoin/src/wallet/send.rs (L413-427)
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
```
