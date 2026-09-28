### Title
`ReceivedOutput::read` accepts an offset that does not match the output’s spend script - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput` binds an arbitrary scalar offset, `TxOut`, and `OutPoint` together without validating that the offset derives the key encoded by `output.script_pubkey`. An unprivileged supplier of serialized `ReceivedOutput` bytes can therefore report an unrelated Taproot UTXO as wallet-controlled. The resulting output can be counted as available funds, but cannot be signed for with the wallet’s base threshold key.

### Finding Description
`ReceivedOutput::read` independently deserializes `offset`, `output`, and `outpoint`, then returns the object without checking their relationship. [1](#0-0) 

The intended relationship is established by `Scanner`, which only creates a `ReceivedOutput` after finding `output.script_pubkey` in its map from registered spend scripts to offsets. [2](#0-1) 

That script-to-offset association is not encoded as a distinct field or reconstructed by the reader. Consequently, bytes containing a valid scalar, a valid foreign `TxOut`, and a valid foreign `OutPoint` deserialize successfully even though the offset was never registered for that script. [1](#0-0) 

`SignableTransaction::new` then trusts every supplied `ReceivedOutput`: it sums the declared output values, copies the offsets, and stores the supplied outputs as prevouts. [3](#0-2) [4](#0-3) 

The compatibility check is deferred until `SignableTransaction::multisig`, where the offset is applied to the wallet key and compared with each prevout’s script. [5](#0-4) 

### Impact Explanation
A foreign or unrelated confirmed Taproot output can be reported as received wallet funds even though the supplied offset does not produce a spend key for its script. The malformed entry influences balance, funding, fee, and change calculations before signing rejects it, producing funds that are reported received but are not spendable by this wallet. [3](#0-2) [5](#0-4) 

### Likelihood Explanation
The malicious input is only serialized wallet data and does not require control of a validator, signing participant, peer, RPC endpoint, or private key. Any integration that accepts or transports `ReceivedOutput::serialize`/`ReceivedOutput::read` values from an untrusted inventory or coordination layer can reach the issue with public bytes. [6](#0-5) 

### Recommendation
Either keep `ReceivedOutput` authentication strictly internal to `Scanner`, or make deserialization context-aware by passing the wallet’s base key and rejecting offsets where:

```rust
p2tr_script_buf(base_key + (ProjectivePoint::GENERATOR * offset)) !=
    Some(output.script_pubkey.clone())
```

The same validation should occur before the value is counted as spendable input in `SignableTransaction::new`, not only during final multisig construction. [7](#0-6) [8](#0-7) 

### Proof of Concept
Conceptually, an attacker serializes scalar `1`, followed by a confirmed foreign Taproot `TxOut`, followed by its real `OutPoint`:

```rust
// networks/bitcoin/src/wallet/mod.rs consumer
let foreign_offset = Scalar::ONE;

let mut encoded = foreign_offset.to_bytes().to_vec();
encoded.extend(bitcoin::consensus::encode::serialize(&foreign_txout));
encoded.extend(bitcoin::consensus::encode::serialize(&foreign_outpoint));

let received = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();
assert_eq!(received.value(), foreign_txout.value.to_sat());
assert_eq!(received.outpoint(), &foreign_outpoint);
```

For a wallet with base `keys`, the accepted object is not spendable under the supplied offset:

```rust
let derived = keys.clone().offset(received.offset()).group_key();
assert_ne!(
  p2tr_script_buf(derived),
  Some(received.output().script_pubkey.clone()),
);
```

Nevertheless, transaction construction counts the foreign value as input capacity:

```rust
let signable = SignableTransaction::new(
  vec![received],
  &[(payment_script, 10_000)],
  None,
  None,
  1,
).unwrap();

// Rejected only after the malformed input was already treated as funding.
assert!(signable.multisig(&keys).is_none());
```

The root cause is the missing association check in `ReceivedOutput::read`, while the later script comparison proves the parsed object was never a valid wallet spend input. [1](#0-0) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-141)
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

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
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
