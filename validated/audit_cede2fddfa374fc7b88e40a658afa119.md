### Title
Deserialized Bitcoin `ReceivedOutput` can claim an unrelated outpoint as spendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and `OutPoint` without proving that the referenced transaction output actually contains the claimed `TxOut` or is spendable by the associated key. An attacker can therefore serialize a forged `ReceivedOutput` whose embedded output pays to the victim’s Taproot script while its outpoint references an unrelated or already-spent output.

### Finding Description
The parser deserializes three independent fields and returns them directly as a `ReceivedOutput`. It performs canonical scalar parsing and syntactic Bitcoin consensus decoding, but no consistency check between `outpoint` and `output`. [1](#0-0) 

`SignableTransaction::new` trusts the embedded `TxOut` as the previous output when calculating input value, collecting offsets, and building inputs. [2](#0-1)  During signing, `SignableTransaction::multisig` checks only that the embedded `TxOut`’s `script_pubkey` matches the threshold key after applying the serialized offset; it does not authenticate that the real blockchain output at `outpoint` is that `TxOut`. [3](#0-2)  The transaction digest later commits to the supplied forged prevout data through `Prevouts::All`. [4](#0-3) 

This is analogous to a file-extension bypass: the object has the expected serialized “shape” and passes the local type checks, but its semantic claim that the outpoint contains the embedded output is not validated.

### Impact Explanation
A forged `ReceivedOutput` can report arbitrary Bitcoin value as belonging to a spendable output even though the referenced on-chain output is unrelated, has a different script or amount, is already spent, or does not exist. Downstream accounting can treat this as received value, and transaction construction/signing can proceed to produce a transaction that Bitcoin consensus will reject.

If the actual referenced output differs in amount or script, the produced Taproot signature is invalid because the sighash commits to the real prevout data. If the actual output is not under Serai’s Taproot script at all, it cannot be spent by the threshold key regardless of the signature.

### Likelihood Explanation
The attacker only needs to cause an application to deserialize attacker-controlled bytes through `ReceivedOutput::read`. The crafted input is deterministic and does not require control of validators, peers, RPC responses, private keys, or threshold collusion. Exploitation does depend on a downstream consumer trusting the deserialized object before independently confirming the outpoint’s contents on-chain.

### Recommendation
Treat `ReceivedOutput` deserialization as syntactic only and revalidate it against the blockchain before using it as spendable value. In particular:

- Fetch the transaction identified by `outpoint.txid`.
- Verify `outpoint.vout` is in range.
- Require the chain’s actual `TxOut` to equal the embedded `TxOut`, including amount and `script_pubkey`.
- Verify the outpoint is unspent and sufficiently confirmed.
- Bind this verification into the type system where possible, such as a separately constructed `ConfirmedReceivedOutput`, so a deserialized object cannot be passed directly to transaction construction.

### Proof of Concept
Conceptually:

```rust
// File: networks/bitcoin/src/wallet/mod.rs
let offset = Scalar::ZERO;
let claimed = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: p2tr_script_buf(group_key).unwrap(),
};

// An outpoint from a real transaction output that is not `claimed`,
// an already-spent outpoint, or a nonexistent outpoint.
let forged_outpoint = OutPoint { txid: unrelated_txid, vout: 0 };

let mut bytes = Vec::new();
bytes.extend(offset.to_bytes());
bytes.extend(serialize(&claimed));
bytes.extend(serialize(&forged_outpoint));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000);
```

Because parsing succeeds, `SignableTransaction::new(vec![forged], ...)` uses the claimed value and outpoint. `multisig` then accepts the forged input as long as the claimed script corresponds to the threshold key plus serialized offset. The resulting transaction references a prevout that does not contain the claimed output, so Bitcoin consensus rejects it while Serai-side accounting may have treated the claimed amount as spendable.

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-184)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L270-284)
```rust
  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
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
