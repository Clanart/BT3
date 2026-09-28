### Title
Untrusted `ReceivedOutput` deserialization bypasses scanner ownership validation - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

`ReceivedOutput::read` reconstructs a supposedly spendable wallet output directly from an attacker-controlled scalar offset, `TxOut`, and `OutPoint`, without verifying that the output was observed on-chain or that its script belongs to the wallet. [1](#0-0) 

### Finding Description

The normal construction path is `Scanner::scan_transaction`, which only creates a `ReceivedOutput` when the transaction output's `script_pubkey` is present in the scanner's registered script map. [2](#0-1) 

`ReceivedOutput::read` bypasses that ownership check entirely because it has no wallet key or scanner context and accepts all three fields independently. [3](#0-2) 

Downstream code treats the decoded object as an owned input: `value` returns the embedded `TxOut` amount, while `SignableTransaction::new` uses the embedded amount, offset, and outpoint to calculate available funds and construct transaction inputs. [4](#0-3) [5](#0-4) 

### Impact Explanation

An unprivileged party who can supply serialized wallet-output bytes can fabricate an apparently received UTXO by combining a valid wallet-controlled Taproot script with a nonexistent or unrelated outpoint. If the forged script does not correspond to the declared offset and wallet key, `SignableTransaction::multisig` rejects it only at signing-machine construction; before that point, the fake output has already been represented as spendable and counted toward input value. [6](#0-5) 

If the attacker uses offset zero and the wallet's known base Taproot script, the script check succeeds, allowing transaction signing to proceed over a fabricated outpoint. [7](#0-6) 

The completed transaction receives Taproot witnesses despite spending a UTXO that was never scanned or proven to exist, so wallet logic can report unavailable funds as received or construct an unbroadcastable transaction. [8](#0-7) 

### Likelihood Explanation

The attack requires an application to deserialize `ReceivedOutput` values from untrusted input rather than treating them as trusted local cache. The public API exposes such a parser, and the attacker only needs the wallet's public Taproot script or another plausible `TxOut` plus an arbitrary scalar and outpoint. [1](#0-0) 

### Recommendation

Do not expose `ReceivedOutput::read` as an unvalidated constructor for untrusted data. Add a scanner/key-bound validation API that checks the decoded `script_pubkey` against the registered script-to-offset mapping and verifies the referenced outpoint against blockchain data before returning a `ReceivedOutput`. If a trusted-storage parser remains necessary, rename or isolate it so callers cannot confuse it with network-input parsing, and document that it requires authenticated storage.

### Proof of Concept

Conceptually, an attacker knowing the wallet's base `ProjectivePoint` can fabricate a zero-offset output paying to that wallet script while naming an outpoint that does not exist:

```rust
use bitcoin::{
  consensus::encode::serialize,
  hashes::Hash,
  Amount, OutPoint, ScriptBuf, TxOut, Txid,
};
use k256::Scalar;
use serai_bitcoin::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

let offset = Scalar::ZERO;
let output = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: p2tr_script_buf(wallet_key).unwrap(),
};
let fake_outpoint = OutPoint::new(Txid::all_zeros(), 0);

let mut encoded = offset.to_bytes().to_vec();
encoded.extend(serialize(&output));
encoded.extend(serialize(&fake_outpoint));

// Succeeds despite fake_outpoint never having appeared in a scanned transaction.
let forged = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();
assert_eq!(forged.value(), 100_000);

// The fake amount is used as available input value.
let signable = SignableTransaction::new(
  vec![forged],
  &[(payment_script, 50_000)],
  None,
  None,
  1,
).unwrap();

// For the wallet's base script and offset zero, this key/script check succeeds.
let machine = signable.multisig(&keys).unwrap();
```

The resulting machine signs `taproot_key_spend_signature_hash` values for the fabricated input and emits witnesses for the nonexistent outpoint. [9](#0-8) [10](#0-9)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-427)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
```
