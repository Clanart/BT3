### Title
Unauthenticated `ReceivedOutput` bytes can alter committed UTXO data and produce unspendable transactions - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an attacker-supplied `offset`, `TxOut`, and `OutPoint` as three independent, unauthenticated fields. `SignableTransaction` then commits the claimed `OutPoint` into the transaction input while committing the separately supplied `TxOut` into `Prevouts::All`.

A malicious party can preserve a valid wallet-controlled `script_pubkey` but alter the amount or replace the `OutPoint` with another output. Transaction construction and multisig creation still succeed because the code only checks that the claimed script corresponds to the offset key. The resulting Taproot signatures commit to false previous-output data, so Bitcoin consensus rejects the transaction. This is analogous to modifying committed collateral after the commitment was established: the output amount/data can be changed independently of the outpoint identifying it.

### Finding Description
`ReceivedOutput::read` deserializes and returns attacker-controlled output data without authenticating that the `TxOut` is the actual output identified by `OutPoint`.

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  // ...
  output = TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
  outpoint =
    OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
  Ok(ReceivedOutput { offset, output, outpoint })
}
``` [1](#0-0) 

`SignableTransaction::new` derives the input value from the claimed `TxOut`, copies the independently claimed `OutPoint` into `previous_output`, and stores the claimed `TxOut` as the previous output.

```rust
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
let tx_ins = inputs
  .iter()
  .map(|input| TxIn {
    previous_output: input.outpoint,
    // ...
  })
``` [2](#0-1) 

```rust
prevouts: inputs.drain(..).map(|input| input.output).collect(),
``` [3](#0-2) 

`SignableTransaction::multisig` validates only that the claimed `script_pubkey` equals the script derived from the threshold key and claimed offset. It does not validate the amount or that the `OutPoint` resolves to that `TxOut`.

```rust
let offset = keys.clone().offset(self.offsets[i]);
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
``` [4](#0-3) 

Finally, signing commits to all claimed previous outputs:

```rust
let prevouts = Prevouts::All(&self.tx.prevouts);
// ...
cache.taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
``` [5](#0-4) 

### Impact Explanation
An attacker who supplies `ReceivedOutput::read` bytes can cause the wallet to treat nonexistent, spent, immature, or incorrectly valued outputs as spendable inputs. If the `OutPoint` is fake or already spent, the transaction is invalid. If the `OutPoint` is real but the amount differs from the chain's actual `TxOut`, the Taproot signature commits to incorrect prevout data and is invalid.

This can make received funds unusable for a signing attempt, create transactions that cannot be broadcast, and repeatedly deny signing progress if poisoned serialized outputs are reprocessed. It matches the accepted impact class of funds reported as received/spendable that are not actually spendable.

### Likelihood Explanation
Any caller accepting serialized `ReceivedOutput` values from an untrusted source is exposed. The attacker does not need a validator key, signature share, private key, RPC access, or control over Bitcoin consensus: malformed serialized bytes are sufficient. Preserving the original wallet `script_pubkey` keeps the modified object passing the offset-key check.

The attack is constrained to contexts that trust serialized `ReceivedOutput` data rather than freshly scanned chain data, and poisoning a valid transaction causes transaction/signing failure rather than direct theft.

### Recommendation
Do not expose `ReceivedOutput::read` as a constructor for trusted spendable state. At minimum:

- Mark deserialization as untrusted and require revalidation against confirmed chain data before `SignableTransaction::new`.
- Resolve each `OutPoint` through Bitcoin consensus data and compare the returned `TxOut` byte-for-byte with the claimed output.
- Track coinbase maturity and spent status before signing.
- Where serialized outputs cross a trust boundary, authenticate them or store a commitment such as `H(outpoint || TxOut || offset)`.
- Ensure every signing attempt derives `prevouts` only from verified chain UTXOs, not merely from bytes packaged with the outpoint.

### Proof of Concept
Conceptual Rust PoC:

```rust
// A real wallet-controlled output was previously obtained by Scanner.
let real: ReceivedOutput = scanner.scan_transaction(&tx).remove(0);
let mut bytes = real.serialize();

// TxOut follows the 32-byte scalar offset. Mutate its consensus-encoded
// amount while retaining the original wallet-controlled script_pubkey.
// Equivalent decoded form:
//
// forged = ReceivedOutput {
//   offset: real.offset(),
//   output: TxOut {
//     value: Amount::from_sat(real.value() + 1),
//     script_pubkey: real.output().script_pubkey.clone(),
//   },
//   outpoint: *real.outpoint(),
// };
//
// Here `bytes[32 ..]` is modified to encode that forged TxOut.

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// This succeeds because only the claimed script is checked against the
// offset-derived group key; the amount and outpoint are not revalidated.
let signable = SignableTransaction::new(
  vec![forged],
  &payments,
  change,
  None,
  fee_per_vbyte,
).unwrap();

let machine = signable.multisig(&threshold_keys).unwrap();
let transaction = complete_frost_signing(machine);

// Bitcoin rejects the result: the Taproot sighash committed to a
// prevout amount different from the actual UTXO's amount.
```

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
