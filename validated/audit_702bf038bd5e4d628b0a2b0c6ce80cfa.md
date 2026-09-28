### Title
Unauthenticated `ReceivedOutput` deserialization permits crediting non-existent Bitcoin UTXOs - ([File: `networks/bitcoin/src/wallet/mod.rs`])

### Summary
`ReceivedOutput::read` accepts arbitrary serialized offsets, `TxOut`s, and `OutPoint`s without proving that the referenced output exists or was produced by `Scanner`. A caller can therefore deserialize a fake input whose script matches a key derived from the supplied offset, causing it to be counted as spendable and used to construct/sign an invalid transaction.

### Finding Description
`ReceivedOutput::read` only performs canonical scalar decoding and Bitcoin consensus decoding, then returns the attacker-controlled `offset`, `output`, and `outpoint` unchanged. [1](#0-0)  `Scanner` normally creates these objects only after matching an on-chain output's `script_pubkey` against registered key-derived scripts. [2](#0-1)  The raw deserializer bypasses that provenance check.

`SignableTransaction::new` subsequently trusts each decoded `ReceivedOutput`: it sums `input.output.value`, copies `input.offset`, and uses `input.outpoint` as the transaction input. [3](#0-2)  `SignableTransaction::multisig` verifies only that the stored script equals the script derived from `keys.offset(offset)`, not that the `OutPoint` identifies a confirmed UTXO containing that `TxOut`. [4](#0-3) 

### Impact Explanation
An attacker can report funds as received even though no corresponding Bitcoin UTXO exists. For a known group key, they choose an offset such that `group_key + offset*G` has even Y, encode the corresponding P2TR script into an arbitrary high-value `TxOut`, and pair it with a fabricated `OutPoint`. The decoded object can satisfy `SignableTransaction::multisig` while spending nothing, producing a transaction that full nodes reject.

### Likelihood Explanation
This requires an integration path that accepts serialized `ReceivedOutput` bytes from an untrusted party rather than only from `Scanner`. Within the stated threat model, the deserialization API is directly exposed to untrusted bytes and performs no authenticity or UTXO-existence validation. A valid matching script is easy to construct because `register_offset` demonstrates that incrementing the scalar reaches an even P2TR key. [5](#0-4) 

### Recommendation
Do not treat `ReceivedOutput::read` as proof of receipt. Re-validate deserialized outputs against confirmed blockchain data and the originating `Scanner`: check that `outpoint` resolves to an unspent output whose exact `TxOut`, including value and `script_pubkey`, matches the serialized value. Prefer authenticating scanner-produced records or storing/receiving them only from trusted local state.

### Proof of Concept
```rust
// Conceptual byte layout accepted by ReceivedOutput::read:
// scalar || consensus_encode(TxOut) || consensus_encode(OutPoint)

let offset = offset_making_group_key_even;
let forged_script = p2tr_script_buf(group_key + (G * offset)).unwrap();

let forged_txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: forged_script,
};
let forged_outpoint = OutPoint {
  txid: nonexistent_or_arbitrary_txid,
  vout: 0,
};

let bytes = offset.to_bytes()
  .concat(serialize(&forged_txout))
  .concat(serialize(&forged_outpoint));

let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
let signable = SignableTransaction::new(
  vec![received],
  &payments,
  change,
  data,
  fee_per_vbyte,
).unwrap();

// This succeeds because only the derived script is checked.
assert!(signable.multisig(&keys).is_some());
```

The resulting transaction commits to the fabricated `OutPoint` and therefore cannot spend the reported funds.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-195)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
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
