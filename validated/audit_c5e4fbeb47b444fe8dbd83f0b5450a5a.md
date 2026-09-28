### Title
Forged `ReceivedOutput` deserialization reports unspendable Bitcoin funds - ([File: `networks/bitcoin/src/wallet/mod.rs`](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an offset, `TxOut`, and `OutPoint` as independent untrusted fields without binding them to a wallet key, a registered scanner offset, or an actual transaction output. An attacker who can supply serialized `ReceivedOutput` bytes can fabricate an apparently spendable wallet output by pairing a valid wallet P2TR script and value with a nonexistent or unrelated outpoint. [1](#0-0) 

### Finding Description
`ReceivedOutput` claims to represent a spendable output and stores the scalar offset needed to spend it. [2](#0-1)  Legitimate construction occurs through `Scanner::scan_transaction`, where the output’s `script_pubkey` is looked up in the registered-script map and its real transaction outpoint is attached. [3](#0-2) 

The deserializer bypasses this invariant. It only parses the three fields and returns `Ok(ReceivedOutput { offset, output, outpoint })`; it does not require the wallet key or scanner, does not verify `output.script_pubkey == p2tr_script_buf(key + offset * G)`, and does not verify that `outpoint` identifies the encoded `TxOut`. [1](#0-0) 

The downstream transaction constructor then trusts the forged fields: it sums `input.output.value`, serializes `input.outpoint` into the transaction input, and copies `input.offset` into the signing data. [4](#0-3)  During multisig setup, the implementation checks only that the supplied previous output’s script matches the key derived using the supplied offset; it does not establish that the referenced outpoint exists or contains that output. [5](#0-4) 

### Impact Explanation
A forged input can cause wallet or accounting logic to report value as received and spendable when the referenced outpoint does not exist, is already spent, or does not contain the encoded output. If spending is attempted, `SignableTransaction::multisig` can accept the forged object because the encoded `TxOut` uses a genuine wallet script, and threshold signing proceeds for a transaction that consensus will reject or that spends a different UTXO than the one accounted for. This maps to improper access control: deserialization grants spendable-output authority without validating the object against the wallet’s authorized scanner/key context and chain state.

### Likelihood Explanation
The attack requires an untrusted party to cause a serialized `ReceivedOutput` to be deserialized, such as through attacker-controlled persistence, synchronization, recovery data, or another boundary that treats these bytes as input. It does not require a key share, validator privilege, malformed point encoding, or protocol collusion. Once deserialization is reachable, constructing the object requires only a known wallet P2TR script and an arbitrary scalar/outpoint.

### Recommendation
Do not expose or consume `ReceivedOutput` deserialization as an unauthenticated assertion of spendability. Bind the output to wallet context by changing the API to reconstruct or validate it through `Scanner`, for example:

- Supply the wallet key or `Scanner` when reading.
- Reject offsets not registered for that wallet.
- Verify `output.script_pubkey` equals `p2tr_script_buf(key + offset * G)`.
- Verify `outpoint` resolves on chain to a transaction output equal to the serialized `TxOut` before marking it spendable.
- Alternatively, persist only the scanner-derived outpoint/offset and require rescanning or authenticated storage for restoration.

### Proof of Concept
Conceptually:

```rust
// Wallet-controlled key K and already-registered spendable offset o.
let wallet_script = p2tr_script_buf(K + (ProjectivePoint::GENERATOR * o)).unwrap();

// Encode attacker-chosen fields.
let forged = {
    let mut bytes = Vec::new();
    bytes.extend(o.to_bytes());
    bytes.extend(serialize(&TxOut {
        value: Amount::from_sat(1_000_000),
        script_pubkey: wallet_script,
    }));
    bytes.extend(serialize(&OutPoint {
        txid: Txid::all_zeros(), // Nonexistent or unrelated output.
        vout: 0,
    }));
    bytes
};

let received = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
assert_eq!(received.value(), 1_000_000);
assert_eq!(received.offset(), o);
assert_eq!(received.outpoint().txid, Txid::all_zeros());
```

`received` is now accepted as a spendable output. Passing it to `SignableTransaction::new` incorporates its claimed value and forged outpoint, and `multisig` passes its script check whenever `o` derives the wallet script. The resulting state reports funds that are not actually spendable.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-96)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
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

**File:** networks/bitcoin/src/wallet/send.rs (L270-282)
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
```
