### Title
Forged `ReceivedOutput` values are accepted and used for crediting and signing - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`ReceivedOutput::read` accepts an attacker-supplied `TxOut` and `OutPoint` without verifying that the `TxOut` is the actual output referenced by the `OutPoint`. Because `SignableTransaction` trusts the serialized value as the spendable input amount and commits that value into the Taproot signature message, forged serialized outputs can cause incorrect balances to be reported or transactions to be signed over false prevout data. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput` represents a spendable UTXO, but its fields are not independently constrained during deserialization. `ReceivedOutput::read` reads an offset, a complete `TxOut`, and an `OutPoint`, then returns them without checking whether the referenced transaction output exists, has the same script, or has the same value. [3](#0-2) [1](#0-0) 

The honest constructor, `Scanner::scan_transaction`, derives the `OutPoint` from the containing transaction and copies the actual transaction output, preserving that invariant. [4](#0-3)  The deserializer does not preserve it: changing only the serialized `TxOut.value` produces a `ReceivedOutput` which reports an arbitrary value while still pointing at the original outpoint. [5](#0-4) 

`SignableTransaction::new` then uses the claimed `TxOut.value` to calculate available input funds and stores the claimed `TxOut` as the prevout for signing. [6](#0-5) [7](#0-6)  `multisig` only verifies that the supplied script corresponds to the expected offset key; it does not validate the claimed amount or output against the blockchain. [8](#0-7)  During signing, these attacker-controlled prevouts are committed through `Prevouts::All`, so the participants sign a BIP-341 message computed from false prevout data. [2](#0-1) 

### Impact Explanation
An unprivileged party who can feed serialized bytes to `ReceivedOutput::read` can make a real small deposit appear as a much larger deposit while retaining the same outpoint identifier. Any downstream crediting path using `value()` will report funds that are not actually spendable at that outpoint. [9](#0-8) [1](#0-0) 

If the forged object is used for spending, the false amount passes `SignableTransaction::new`'s accounting checks and is committed to the signature hash. The resulting transaction spends the real outpoint but is signed as though its value were the forged value, producing signatures for an unintended transaction context and an invalid Bitcoin transaction when the real prevout amount differs. [10](#0-9) [2](#0-1) 

### Likelihood Explanation
The attack only requires control over bytes passed to `ReceivedOutput::read`; no validator key, peer compromise, malformed curve point, or private-field access is needed. The encoding is easy to forge because the amount is a fixed little-endian field beginning immediately after the 32-byte scalar offset. [11](#0-10) 

The honest scanner already provides a valid outpoint and script template, so an attacker can mutate a genuine serialized `ReceivedOutput` rather than construct a valid transaction themselves. [4](#0-3) 

### Recommendation
Do not treat `ReceivedOutput::read` as proof that the encoded `TxOut` belongs to the encoded `OutPoint`. Before crediting or signing, resolve the `OutPoint` against a trusted chain view and replace or compare the entire decoded `TxOut` with the canonical UTXO data. Prefer serializing only the outpoint and offset for untrusted transport, or provide an explicitly trusted deserialization API and a separate untrusted claim type that must be validated. [11](#0-10) [6](#0-5) 

### Proof of Concept
1. Obtain a genuine serialized `ReceivedOutput` for a small Taproot deposit.
2. Mutate bytes `32 .. 40`, the consensus-encoded `TxOut.value`, from the real amount to a larger amount while leaving the script and outpoint unchanged.
3. Pass the mutated bytes to `ReceivedOutput::read`; deserialization succeeds and `value()` reports the forged amount.
4. Pass the result to `SignableTransaction::new`; it uses the forged amount for solvency accounting and commits it as a prevout during signing. [1](#0-0) [6](#0-5) 

```rust
// Mutate a genuine serialized ReceivedOutput.
let mut forged = genuine.serialize();
forged[32 .. 40].copy_from_slice(&(genuine.value() * 100).to_le_bytes());

let forged = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
assert_eq!(forged.outpoint(), genuine.outpoint());
assert_eq!(forged.value(), genuine.value() * 100);

let tx = SignableTransaction::new(
  vec![forged],
  &[(payment_script, payment_amount)],
  change_script,
  None,
  fee_per_vbyte,
).unwrap();
```

The parsed object reports funds that the referenced outpoint does not contain, and the created signing machine commits to the forged prevout amount through `Prevouts::All`. [7](#0-6) [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L115-141)
```rust
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
  }

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-210)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-180)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-220)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-281)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
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
