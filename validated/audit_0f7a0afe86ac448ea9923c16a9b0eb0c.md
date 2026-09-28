### Title
Forged `ReceivedOutput` records can report nonexistent funds as wallet-controlled - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an independently serialized scalar offset, `TxOut`, and `OutPoint` without authenticating that the outpoint exists or actually contains the claimed output. A caller that deserializes attacker-controlled wallet data can therefore create an output that appears spendable by the scanner but is not backed by the referenced UTXO. [1](#0-0) 

### Finding Description
`ReceivedOutput` contains three independent claims: the key offset, the claimed `TxOut`, and the claimed `OutPoint`. [2](#0-1) 

Its deserializer validates only that the scalar is canonical and that the `TxOut` and `OutPoint` are consensus-decodable; it does not prove that `outpoint` resolves to `output`. [3](#0-2) 

Under normal operation, `Scanner::scan_transaction` creates this association by taking the real `TxOut` and deriving the outpoint from the containing transaction and output index. [4](#0-3) 

When attacker-controlled bytes are passed to `ReceivedOutput::read`, that invariant is bypassed. `SignableTransaction::new` then trusts both the claimed value and claimed outpoint when constructing the transaction. [5](#0-4) 

The later `multisig` check confirms only that `keys.offset(offset).group_key()` produces the claimed `script_pubkey`; it does not check that the referenced UTXO exists or equals the claimed `TxOut`. [6](#0-5) 

### Impact Explanation
An attacker can submit bytes describing a large `TxOut` paying a legitimate scanner script while attaching a nonexistent or unrelated `OutPoint`. [7](#0-6) 

The wallet will treat the forged object as a received output with the attacker-chosen value, and transaction construction will use that nonexistent UTXO as an input. [8](#0-7) 

The resulting transaction either commits to a false previous output or to a false amount in the Taproot sighash, making the signed transaction unspendable and causing funds to be reported or accounted for when they cannot actually be spent. [9](#0-8) 

### Likelihood Explanation
This requires an attacker to cause an application to deserialize attacker-controlled bytes with `ReceivedOutput::read` and then consume the result as a wallet output. That is the public-input path under consideration, but applications that only construct `ReceivedOutput` through `Scanner::scan_transaction` preserve the necessary outpoint/output association and are not affected. [10](#0-9) 

The issue is therefore a Medium-severity integrity failure rather than direct key compromise: it enables false wallet accounting and construction of transactions that Bitcoin will reject, but does not by itself authorize spending another party's real UTXO. [11](#0-10) 

### Recommendation
Treat deserialized `ReceivedOutput` values as unauthenticated claims. Before accounting for or spending one, load the transaction identified by `outpoint.txid`, verify that `outpoint.vout` is in range, and require the chain's actual `TxOut` to equal the stored `output`. [7](#0-6) 

Prefer making `ReceivedOutput` constructible only by `Scanner`, or include authenticated scanner provenance in the serialized form. At minimum, `SignableTransaction::new` or `multisig` should revalidate the outpoint and `TxOut` against a trusted Bitcoin view rather than trusting both fields independently. [6](#0-5) 

### Proof of Concept
```rust
// Conceptual PoC for an API consuming untrusted serialized wallet outputs.
use bitcoin::{Amount, OutPoint, TxOut};
use frost::curve::{Ciphersuite, Secp256k1};

// `scanner_script` is a script already produced by Scanner for the wallet key,
// for example the P2TR script for offset zero.
let forged_scalar = [0u8; 32]; // canonical scalar zero
let forged_txout = TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: scanner_script.clone(),
};
let forged_outpoint = OutPoint::null(); // or any nonexistent/unrelated outpoint

let mut bytes = forged_scalar.to_vec();
bytes.extend(bitcoin::consensus::encode::serialize(&forged_txout));
bytes.extend(bitcoin::consensus::encode::serialize(&forged_outpoint));

let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(received.value(), 1_000_000);
assert_eq!(received.output().script_pubkey, scanner_script);
```

`SignableTransaction::new` accepts this forged record because it uses `received.outpoint` as the transaction input and `received.output` as the committed previous output. [8](#0-7) 

`multisig` accepts it whenever the chosen offset produces the claimed scanner script, such as a zero offset for the scanner's base P2TR output, but the referenced outpoint remains nonexistent or unrelated on-chain. [6](#0-5)

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
