### Title
Untrusted `ReceivedOutput` bytes can fabricate unspendable Bitcoin deposits - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` deserializes the key offset, full `TxOut`, and `OutPoint` as three independent attacker-controlled fields without validating that the referenced on-chain output exists or contains the claimed script and amount. [1](#0-0) 

### Finding Description
`ReceivedOutput::read` accepts any canonical scalar for `offset`, any consensus-decodable `TxOut`, and any consensus-decodable `OutPoint`, then constructs the “spendable output” directly. [1](#0-0)  Unlike outputs produced by `Scanner::scan_transaction`, these fields are not proven to have been derived from a real transaction output or a registered scanner script. [2](#0-1) 

Downstream, `SignableTransaction::new` trusts the forged `TxOut` as the input value and the forged `OutPoint` as the transaction input. [3](#0-2)  The claimed `TxOut` is also committed as the Taproot prevout during signing. [4](#0-3)  The only local ownership check is that the supplied `offset` derives the claimed `script_pubkey`; an attacker can satisfy that check while selecting a nonexistent or unrelated outpoint and an arbitrary amount. [5](#0-4) 

### Impact Explanation
A caller that accepts serialized `ReceivedOutput` values from an untrusted party can report or account for funds that are not actually spendable by Serai’s key. The fabricated object can pass the signing-machine key/script check and cause threshold signers to produce signatures for an invalid Bitcoin transaction spending a nonexistent or mismatched UTXO. This matches the accepted impact of funds reported received that are not spendable.

### Likelihood Explanation
The bytes are fully attacker-controlled and compact: the attacker chooses a valid offset, constructs a matching P2TR script, sets an arbitrary amount, and chooses any txid/vout. Exploitation requires an application or service boundary to deserialize attacker-supplied `ReceivedOutput` values rather than only consuming outputs returned by `Scanner`; that reachability assumption is explicitly part of the audited input surface.

### Recommendation
Do not let `ReceivedOutput::read` act as an unchecked constructor for trusted wallet state. Either make deserialization private to scanner-produced data, or change it to require the expected base key/scanner context and reject values whose `script_pubkey` does not equal `p2tr_script_buf(base_key + offset * G)`. Before crediting or spending the result, also verify the `OutPoint` exists on-chain and that its actual `TxOut` exactly matches the deserialized script and amount.

### Proof of Concept
```rust
use bitcoin::{
  consensus::serialize,
  hashes::Hash,
  Amount, OutPoint, ScriptBuf, Txid, TxOut,
};
use k256::Scalar;
use frost::{curve::Secp256k1, ThresholdKeys};
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

fn forge_received_output(keys: &ThresholdKeys<Secp256k1>) -> ReceivedOutput {
  // Zero is a valid offset for the untweaked scanner script.
  let offset = Scalar::ZERO;
  let script: ScriptBuf = p2tr_script_buf(keys.group_key()).unwrap();

  // This script matches the key, but the txid/vout and amount are fabricated.
  let fake_txout = TxOut {
    value: Amount::from_sat(100_000_000),
    script_pubkey: script,
  };
  let fake_outpoint = OutPoint::new(Txid::all_zeros(), 0);

  let mut bytes = offset.to_bytes().to_vec();
  bytes.extend(serialize(&fake_txout));
  bytes.extend(serialize(&fake_outpoint));

  ReceivedOutput::read(&mut bytes.as_slice()).unwrap()
}
```

The forged object reports a 1 BTC input and can be placed into `SignableTransaction::new`; `multisig(keys)` accepts the key/script relationship, while the resulting transaction remains invalid because no corresponding UTXO exists. [6](#0-5)

### Citations

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
