### Title
Untrusted `ReceivedOutput` serialization can understate a real UTXO and burn the difference as fees - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an attacker-controlled `TxOut` value and `OutPoint` without proving that the pair exists on-chain or that the encoded amount is the UTXO's actual amount. `SignableTransaction::new` trusts this amount when calculating available input value, and `multisig` verifies only that the encoded script corresponds to the selected key offset. A maliciously modified serialized output can therefore reference a real spendable UTXO while claiming it contains fewer satoshis than it actually does.

### Finding Description
`ReceivedOutput::read` deserializes the offset, complete `TxOut`, and `OutPoint`, then returns them without validating the referenced UTXO or its amount. [1](#0-0) 

The transaction builder sums the supplied `output.value` fields as authoritative input value. [2](#0-1) 

During signing, the implementation verifies only that the encoded `script_pubkey` equals the Taproot script generated from the participant key plus the stored offset; it does not compare the encoded amount or outpoint to blockchain state. [3](#0-2) 

Finally, the fabricated `prevouts`, including their attacker-controlled amounts, are committed through `Prevouts::All` and used in each Taproot signature hash. [4](#0-3) 

This is analogous to crediting an expected transfer amount rather than the actual amount: serialized wallet accounting can differ from the actual UTXO value accepted by Bitcoin consensus.

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` bytes can copy a valid encoded output, preserve its real `OutPoint` and authorized `script_pubkey`, and lower its `TxOut.value`.

If the actual UTXO is worth `A` but the encoded value claims `C < A`, transaction construction accounts for only `C` and creates outputs based on that understated amount. Bitcoin consensus nevertheless spends the real `A`; the unaccounted `A - C` remainder becomes additional miner fees. This produces a valid signed transaction that permanently destroys wallet value.

Overstating the amount causes the final transaction to be consensus-invalid, while understating a real UTXO produces the more serious excess-fee outcome.

### Likelihood Explanation
The attack requires untrusted bytes to reach `ReceivedOutput::read` and the returned object to be accepted as a wallet input. No private key material, validator privilege, malicious Bitcoin node, or collusion is required for the malformed object itself. The script check does not detect the tampering because the attacker preserves the original script and changes only the amount field.

### Recommendation
Do not treat `ReceivedOutput::read` as proof of ownership or value. Before scheduling or signing:

1. Resolve every `outpoint` against confirmed blockchain state.
2. Reject the input unless the chain's `TxOut` exactly matches the encoded `TxOut`, including both value and script.
3. Prefer storing only the offset and outpoint, then reconstructing the trusted `TxOut` from verified chain data.
4. If serialized outputs must cross trust boundaries, authenticate them or bind them to a scanner-produced database entry.

### Proof of Concept
The serialization layout is:

```text
32-byte scalar offset
consensus-encoded TxOut:
  8-byte little-endian value
  compact-size script length
  script bytes
consensus-encoded OutPoint
```

Given an authentic serialized `ReceivedOutput` for a real UTXO worth `A`, an attacker changes only the 8-byte `TxOut.value` field to `C < A`. Parsing succeeds because there is no checksum, authentication, or chain validation:

```rust
let forged = ReceivedOutput::read(&mut attacker_bytes.as_slice())?;
assert_eq!(forged.outpoint(), &real_outpoint);
assert_eq!(forged.output().script_pubkey, real_script);
assert_eq!(forged.value(), C); // Actual UTXO value is A > C.
```

`SignableTransaction::new(vec![forged], payments, change, data, fee_rate)` calculates input capacity as `C`, while `multisig` accepts the input because the unchanged script still matches the offset-derived key. The signed transaction spends the real UTXO worth `A`, leaving `A - C` above the accounted input value to miners as excess fee.

### Citations

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
