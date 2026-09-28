### Title
Deserialized `ReceivedOutput` can claim arbitrary outpoints as wallet-controlled funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

`ReceivedOutput::read` deserializes an offset, a `TxOut`, and an `OutPoint` without establishing that the `OutPoint` actually refers to the supplied `TxOut` or that the output was discovered by a `Scanner`. This lets attacker-controlled bytes create a syntactically valid “received output” that refers to an unrelated or nonexistent UTXO.

### Finding Description

`Scanner` is the component that establishes the expected provenance invariant: it maps registered P2TR scripts to offsets and constructs a `ReceivedOutput` only for an output actually present at a transaction’s enumerated `vout`. [1](#0-0) [2](#0-1) 

`ReceivedOutput::read` bypasses that invariant. It accepts any canonical scalar as `offset`, any attacker-supplied `TxOut`, and any attacker-supplied `OutPoint`, then returns them as a single trusted object. [3](#0-2) 

Later, `SignableTransaction::multisig` validates only that the declared previous output’s `script_pubkey` matches the key derived from the supplied offset; it does not validate that the referenced on-chain UTXO has that script or value. [4](#0-3)  The generated signature then commits to the forged `prevouts` data using `Prevouts::All`, so the resulting transaction is invalid whenever the actual UTXO differs. [5](#0-4) 

### Impact Explanation

An attacker who can supply serialized `ReceivedOutput` bytes can cause the caller to record or act on funds that were never received and cannot be spent. The forged record can be converted into a `SignableTransaction` and signed, but Bitcoin consensus will reject it because the actual referenced UTXO does not contain the claimed script and amount. This creates false balance/accounting state and may stall or misdirect downstream payment scheduling.

### Likelihood Explanation

The attack requires an attacker to control bytes passed to `ReceivedOutput::read` or an embedding decoder such as Bitcoin `Output::read`. It does not require access to private keys, validator privileges, malformed curve encodings, or a malicious Bitcoin node. Public knowledge of the target group key is sufficient to select an offset and derive the corresponding P2TR script.

### Recommendation

Do not deserialize `ReceivedOutput` into a state indistinguishable from a scanner-produced output. Either:

- make `ReceivedOutput::read` private to trusted persistence paths;
- store and validate a binding to the containing transaction/block;
- re-resolve every `OutPoint` against the chain before treating the object as spendable; or
- split the type into an untrusted `ClaimedOutput` and a scanner-produced `ReceivedOutput`, allowing conversion only after on-chain verification.

### Proof of Concept

```rust
use bitcoin::{
  consensus::Encodable,
  hashes::Hash,
  Amount, OutPoint, ScriptBuf, TxOut, Txid,
};
use frost::curve::{Ciphersuite, Secp256k1};
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

// `group_key` is the public threshold key being impersonated.
let offset = k256::Scalar::ONE;
let claimed_script = p2tr_script_buf(group_key + (k256::ProjectivePoint::GENERATOR * offset))
  .unwrap();

let forged = ReceivedOutput::read(&mut [
  offset.to_bytes().as_slice(),
  &bitcoin::consensus::serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: claimed_script,
  }),
  // An unrelated or nonexistent UTXO.
  &bitcoin::consensus::serialize(&OutPoint {
    txid: Txid::all_zeros(),
    vout: 0,
  }),
].concat().as_slice()).unwrap();

// This reports 100,000 sats and passes the later script/key consistency check,
// despite the outpoint not being an output controlled by the threshold key.
assert_eq!(forged.value(), 100_000);
```

The forged object satisfies `SignableTransaction::multisig`’s script check, but the resulting transaction cannot spend `Txid::all_zeros():0` because that referenced UTXO does not contain the claimed `TxOut`.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L153-165)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L180-213)
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
  }

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

**File:** networks/bitcoin/src/wallet/send.rs (L373-387)
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
```
