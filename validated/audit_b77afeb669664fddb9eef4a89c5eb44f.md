### Title
Deserialized Bitcoin outputs can claim unspendable or nonexistent funds - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and outpoint without authenticating that the tuple was produced by `Scanner` or proving that the offset controls the output’s `script_pubkey`. Consequently, untrusted serialized bytes can create a `ReceivedOutput` which reports funds as received even though the configured threshold key cannot spend them.

### Finding Description
`ReceivedOutput` stores the spend offset, the asserted prevout, and the asserted outpoint as independent fields. [1](#0-0) 

`ReceivedOutput::read` only checks scalar canonicity and consensus decodability; it has no scanner context and performs no relationship check between `offset`, `output.script_pubkey`, and `outpoint`. [2](#0-1) 

In contrast, legitimate outputs are only constructed by `Scanner::scan_transaction` after matching the transaction’s `script_pubkey` against a registered output script, which supplies the corresponding offset. [3](#0-2) 

The malformed object is then trusted as a spendable input by `SignableTransaction::new`, which copies its value into the input total, its offset into the signing context, its outpoint into a transaction input, and its `TxOut` into `Prevouts::All`. [4](#0-3) [5](#0-4) 

The later `multisig` check compares the threshold key adjusted by the supplied offset with the supplied `script_pubkey`, so an inconsistent tuple is not actually spendable and causes `multisig` to return `None`. [6](#0-5) 

### Impact Explanation
An attacker able to supply serialized `ReceivedOutput` data can cause a downstream consumer to treat nonexistent, foreign-owned, incorrectly valued, or incorrectly offset outputs as received wallet funds. `value()` reports the attacker-controlled `TxOut` amount directly. [7](#0-6) 

This can inflate balances, credit deposits that cannot be spent, or cause transaction construction to incorporate a forged outpoint or prevout amount. If the `TxOut` does not match the actual blockchain prevout, the Taproot signature hash is also based on incorrect prevout data, resulting in an invalid transaction after signing. [8](#0-7) 

### Likelihood Explanation
The input requirements are only canonical scalar encoding plus consensus-decodable `TxOut` and `OutPoint` values, all of which an unprivileged remote party can construct. [9](#0-8) 

No private data, malformed curve encoding, validator cooperation, or control over the real UTXO is required. Exploitation depends on an application accepting serialized `ReceivedOutput` records from an untrusted source rather than exclusively persisting objects produced by its own `Scanner`. [3](#0-2) 

### Recommendation
Treat serialized `ReceivedOutput` as an unauthenticated assertion rather than a spendability proof.

At minimum, deserialization should require the scanner context and rebuild the expected `script_pubkey` from the configured group key and decoded offset before accepting the record. Consumers should also resolve the decoded `OutPoint` against the confirmed chain/UTXO set and compare the resulting actual `TxOut` with the serialized `TxOut` before accounting or signing.

### Proof of Concept
Conceptually:

```rust
// Construct bytes containing:
//   offset = 0
//   output = TxOut {
//     value: Amount::from_sat(1_000_000),
//     script_pubkey: <an unrelated valid P2TR script>,
//   }
//   outpoint = <an arbitrary or foreign-owned OutPoint>
//
// ReceivedOutput::read accepts all three fields independently.
let forged = ReceivedOutput::read(&mut forged_bytes.as_slice()).unwrap();

// The object now reports one million sats as received.
assert_eq!(forged.value(), 1_000_000);

// Transaction construction treats it as an input.
let tx = SignableTransaction::new(
  vec![forged],
  &[(payment_script, 500_000)],
  None,
  None,
  1,
).unwrap();

// But the configured wallet cannot spend it because offset 0 does not
// produce the unrelated script_pubkey.
assert!(tx.multisig(&wallet_keys).is_none());
```

The same primitive can instead pair a real wallet outpoint with an inflated `TxOut.value`; the object will report and account for the inflated amount, while the signed transaction commits to incorrect prevout data. [8](#0-7)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
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
    })
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
