### Title
Deserialized Bitcoin outputs are trusted as spendable without verifying their outpoints - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` reconstructs the spend offset, claimed `TxOut`, and `OutPoint` as three independent fields without verifying that the outpoint exists or that its on-chain output matches the serialized value and script. A caller that accepts serialized `ReceivedOutput` data can therefore treat nonexistent or incorrectly described UTXOs as spendable inputs. [1](#0-0) 

### Finding Description
`ReceivedOutput` represents “a spendable output” and stores the scalar offset, `TxOut`, and `OutPoint`. [2](#0-1)  `ReceivedOutput::read` only performs canonical scalar decoding and consensus decoding of the two Bitcoin objects; it has no base-key context and performs no integrity check connecting the offset to the script or the outpoint to an actual UTXO. [3](#0-2)  Legitimate objects produced by `Scanner::scan_transaction` are internally consistent because the scanner derives the outpoint from the containing transaction and selects the script’s registered offset. [4](#0-3)  That invariant is not enforced when an equivalent object is supplied through the public deserializer.

`SignableTransaction::new` trusts the claimed outpoint and output when constructing inputs and prevouts. [5](#0-4)  The later `multisig` check verifies only that the tweaked group key produces the serialized script; it does not verify that `outpoint` references that script or amount on-chain. [6](#0-5) 

### Impact Explanation
An unprivileged party who can cause crafted `ReceivedOutput` bytes to be accepted can report a nonexistent output, an already-spent output, or a correctly-scripted output with an invented amount as funds available to the wallet. The wallet can then account for those funds and produce a locally signed transaction whose input is invalid under Bitcoin consensus because the referenced prevout does not exist or does not have the claimed `TxOut`. This is “funds reported received that are not spendable,” rather than only malformed input rejection.

### Likelihood Explanation
The serialized format is fully unauthenticated: the offset and script can be copied from a real scanner result while changing only the outpoint or amount. The existing spend-path validation permits that mutation because it checks the script/offset relation but not blockchain membership or the true prevout contents. Exploitation requires the integration to accept serialized `ReceivedOutput` values from an untrusted source or mutable storage; scanner-generated objects themselves do not exhibit this inconsistency.

### Recommendation
Treat `ReceivedOutput` serialization as a claim, not proof of spendability. Before accounting or signing:

- Re-derive the expected script from the wallet’s base key and claimed offset and require it to equal `output.script_pubkey`.
- Resolve `outpoint` against confirmed blockchain data and require the resolved `TxOut` to equal the serialized `TxOut` exactly, including value.
- Reject coinbase outputs until mature if immediate spendability is required.
- Prefer storing authenticated scanner state or a transaction reference that can be independently rescanned instead of trusting standalone serialized UTXOs.

### Proof of Concept
```rust
// Obtain a valid scanned output for the wallet's normal zero-offset address.
let real = scanner.scan_transaction(&real_tx).remove(0);

let mut forged = real.serialize();

// Preserve the valid offset and TxOut prefix, replacing the trailing OutPoint
// with a nonexistent txid/vout. ReceivedOutput::read accepts it.
let outpoint_len = 36;
forged[(forged.len() - outpoint_len)..].copy_from_slice(&serialize(&fake_outpoint));

let output = ReceivedOutput::read(&mut forged.as_slice()).unwrap();

// This accepts the fake outpoint and embedded claimed TxOut.
let signable =
  SignableTransaction::new(vec![output], &payments, change, None, fee).unwrap();

// If the script was left unchanged, this also passes the offset/script check.
let machine = signable.multisig(&keys).unwrap();

// The resulting signed transaction is not spendable: its input references a
// nonexistent prevout or commits to an incorrect claimed prevout amount.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-133)
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
}

impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-211)
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
