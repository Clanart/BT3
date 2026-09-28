### Title
Forged `ReceivedOutput` deserialization fabricates wallet funds and produces an unspendable signed transaction - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an attacker-controlled scalar offset, `TxOut`, and `OutPoint` without proving that the outpoint exists or that the encoded `TxOut` is the output currently committed at that outpoint. Because `SignableTransaction` trusts the embedded value, outpoint, and prevout, forged bytes can report wallet-controlled funds that do not exist and cause a threshold signing flow for a transaction which Bitcoin will reject.

### Finding Description
Legitimate `ReceivedOutput`s are produced by `Scanner::scan_transaction`, where the output is copied from a transaction and the outpoint is calculated from that transaction's real txid and output index. [1](#0-0)  The deserializer bypasses that provenance: it reads a canonical scalar, consensus-decodes an arbitrary `TxOut`, consensus-decodes an arbitrary `OutPoint`, and returns all three as a spendable output. [2](#0-1) 

`SignableTransaction::new` then uses the deserialized `TxOut` value as available input balance and the deserialized `OutPoint` as the transaction input. [3](#0-2)  The only wallet-key check in `multisig` is that the supplied prevout's `script_pubkey` equals `p2tr_script_buf(key + offset * G)`; it does not verify that the supplied `OutPoint` exists or resolves to that `TxOut`. [4](#0-3)  The signing machine then creates a BIP-341 signature hash using the attacker-supplied `Prevouts::All`, so the threshold group signs the fabricated input commitment. [5](#0-4) 

### Impact Explanation
An unprivileged party who can submit serialized wallet-output bytes can fabricate an apparent received balance by selecting the wallet's public P2TR script, a zero or otherwise usable offset, an arbitrary amount, and a nonexistent or mismatched outpoint. The record reports the attacker-selected amount through `ReceivedOutput::value`, passes the local key-to-script check, and can drive transaction construction and threshold signing for an input that is not spendable on chain. [6](#0-5) [7](#0-6) 

This is an integrity failure in wallet state and transaction authorization: the resulting transaction may contain a validly structured threshold signature, but it cannot spend the claimed input because either the outpoint does not exist or the actual prevout differs from the signed `Prevouts::All` data. [5](#0-4) 

### Likelihood Explanation
The attacker only needs the wallet's public group key or deposit script and the ability to feed bytes to `ReceivedOutput::read`; no private key, validator privilege, network control, or cryptographic break is required. [2](#0-1)  A forged output paying the wallet script passes `multisig`'s only consistency check, while an invented or mismatched outpoint is not authenticated against the chain. [4](#0-3) 

### Recommendation
Do not treat deserialized `ReceivedOutput`s as authenticated scanner results. Before balance accounting or signing, resolve each `outpoint` against a confirmed transaction and require the chain's actual `TxOut` to equal the supplied `TxOut`; additionally require `script_pubkey == p2tr_script_buf(expected_key + offset * G)`. Persist scanner outputs through authenticated storage or include a trusted-source/authenticity check around deserialization so arbitrary bytes cannot become spendable wallet records.

### Proof of Concept
Conceptual test demonstrating that public values and attacker-controlled bytes reach signing:

```rust
use bitcoin::{
  Amount, OutPoint, TxOut, Txid,
  consensus::serialize,
  hashes::Hash,
};
use k256::Scalar;
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

// Public wallet group key and its normal P2TR output script.
let wallet_script = p2tr_script_buf(group_key).unwrap();

// Claim a large wallet-controlled output, but point it at a nonexistent outpoint.
let fake_prevout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: wallet_script,
};
let fake_outpoint = OutPoint::new(Txid::all_zeros(), 0);

// ReceivedOutput serialization format: scalar || TxOut || OutPoint.
let mut bytes = Scalar::ZERO.to_bytes().to_vec();
bytes.extend(serialize(&fake_prevout));
bytes.extend(serialize(&fake_outpoint));

let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(received.value(), 1_000_000);

// The fabricated amount is accepted as spendable input value.
let spend = SignableTransaction::new(
  vec![received],
  &[(attacker_script, 900_000)],
  None,
  None,
  1,
).unwrap();

// The script matches the wallet key plus zero offset, so the transaction reaches
// threshold signing despite the nonexistent/mismatched outpoint.
assert!(spend.multisig(&wallet_keys).is_some());
```

`ReceivedOutput::read` provides no provenance check, `SignableTransaction::new` trusts the encoded value and outpoint, and `multisig` validates only the script against the offset-adjusted key. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L115-134)
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-284)
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
  }

  /// Returns the TX ID of the transaction this will create.
  pub fn txid(&self) -> [u8; 32] {
    let mut res = self.tx.compute_txid().to_byte_array();
    res.reverse();
    res
  }

  /// Returns the transaction, sans witness, this will create if signed.
  pub fn transaction(&self) -> &Transaction {
    &self.tx
  }

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
