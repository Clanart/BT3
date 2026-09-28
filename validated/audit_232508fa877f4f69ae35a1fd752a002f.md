### Title
Duplicate `ReceivedOutput`s are treated as distinct inputs, causing threshold signers to produce an invalid Bitcoin transaction - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` accepts a public `Vec<ReceivedOutput>` but never checks that two entries do not reference the same `OutPoint`. Each entry is independently counted toward available funds, serialized as a transaction input, assigned a signing machine, and included in `Prevouts::All`. This is analogous to repeatedly unlocking the same token identifier: the same resource is consumed multiple times because the input is not marked as already used or rejected as duplicated.

### Finding Description
`SignableTransaction::new` computes `input_sat` by summing every `ReceivedOutput::output.value`, builds one `TxIn` per `input.outpoint`, and stores every `input.output` in `prevouts`, without deduplicating `input.outpoint` [1](#0-0) . `multisig` then creates one `Schnorr` signing machine for every transaction input [2](#0-1) . During signing, every index is signed against the complete prevout list [3](#0-2) . `complete` then places a signature into every duplicated transaction input [4](#0-3) .

Because Bitcoin consensus requires each non-coinbase input to spend a distinct prevout, two entries with the same `OutPoint` produce a transaction that can never be confirmed. Nonetheless, Serai's wallet treats the duplicated UTXO as spendable balance twice and drives the threshold protocol to completion.

### Impact Explanation
An unprivileged party able to supply transaction data or serialized `ReceivedOutput`s to this API can cause validators to sign a payment that appears funded but is consensus-invalid. For example, one 1,000-satoshi output supplied twice is accounted as 2,000 satoshis, allowing construction and signing of a transaction paying more value than is actually available. The resulting transaction wastes a signing attempt and cannot pay the recipient, denying successful settlement even though the signer completed the protocol.

This does not merely reject malformed input: it produces signatures for an unintended, unbroadcastable transaction. Since each duplicate is separately passed through the FROST signing flow, the invalidity is preserved all the way through `TransactionSignatureMachine::complete`.

### Likelihood Explanation
The vulnerable path is directly reachable through `SignableTransaction::new(Vec<ReceivedOutput>, ...)` and through untrusted bytes supplied to `ReceivedOutput::read`, which accepts the offset, `TxOut`, and `OutPoint` without comparing the outpoint to any other output [5](#0-4) . No blockchain control, malformed curve point, malicious validator, private-key compromise, or trusted-RPC manipulation is required beyond causing duplicate output records to enter the input list.

The failure is deterministic whenever the same `OutPoint` appears more than once.

### Recommendation
Maintain a `HashSet<OutPoint>` while constructing `SignableTransaction` and reject any input whose `outpoint` was already present. Add a `DuplicateInput` error to `TransactionError`, check `inputs` before summing their value or generating `tx_ins`, and test that duplicating a `ReceivedOutput` is rejected before threshold signing begins.

### Proof of Concept
1. Construct or deserialize one valid `ReceivedOutput` with `outpoint == O` and value `V`.
2. Pass `vec![output.clone(), output]` to `SignableTransaction::new`.
3. Request a payment greater than `V` but less than `2V - fee`.
4. `input_sat` is calculated as `2V`, so the `NotEnoughFunds` check passes.
5. The resulting transaction contains two `TxIn`s with `previous_output == O`.
6. `multisig` creates two signing machines, `sign` generates two per-input signature shares, and `complete` emits a fully signed transaction.
7. Bitcoin consensus rejects the transaction because `O` is spent twice, despite Serai having counted it twice and completed threshold signing.

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
