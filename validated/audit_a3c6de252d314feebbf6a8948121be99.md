### Title
Serialized `ReceivedOutput` accepts unattested UTXO claims as spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts three independent attacker-controlled claims—a scalar offset, a complete `TxOut`, and an `OutPoint`—without proving that the outpoint exists or that the serialized `TxOut` is the output contained at that outpoint. [1](#0-0)  `SignableTransaction::new` then treats the claimed `TxOut` value as spendable input value and uses the claimed outpoint as the transaction input. [2](#0-1) 

### Finding Description
The analog to accepting a mislabeled upload is accepting a serialized “received output” solely because its fields are individually decodable, without validating the claimed object against Bitcoin’s authoritative UTXO data. `Scanner::scan_transaction` normally creates this invariant by deriving the `ReceivedOutput` from an actual transaction output and computing its outpoint from that transaction. [3](#0-2)  The deserializer bypasses that construction path and manufactures the same type from unauthenticated bytes. [1](#0-0) 

`SignableTransaction::multisig` checks only that `p2tr_script_buf(keys.group_key() + offset)` equals the claimed `TxOut`’s `script_pubkey`. [4](#0-3)  An attacker who knows the public group key can satisfy that check with offset zero and a fabricated `TxOut`, while claiming a nonexistent or unrelated `OutPoint` and an arbitrary value. [5](#0-4)  The transaction-signing path then commits all supplied prevouts into each Taproot sighash, so malformed input metadata can cause a threshold signature over a transaction that cannot spend the reported funds. [6](#0-5) 

### Impact Explanation
An integration that accepts serialized `ReceivedOutput` values from peers, storage, or another untrusted boundary can report attacker-chosen balances as received even though no corresponding spendable Bitcoin UTXO exists. [7](#0-6)  If those objects are subsequently scheduled, they can induce threshold signing of an invalid transaction and disrupt wallet operation or corrupt deposit accounting. [2](#0-1) [8](#0-7) 

### Likelihood Explanation
This is reachable with only public information: the attacker needs the public group key to form the expected P2TR script and can choose the offset, value, and outpoint bytes freely. [9](#0-8)  Exploitation requires an integrator to treat deserialized `ReceivedOutput` values as authoritative rather than exclusively using objects produced by `Scanner` against confirmed blocks. [3](#0-2) 

### Recommendation
Do not let `ReceivedOutput::read` silently mint an authoritative spendable-output object. Either make deserialization produce an unverified pending output that must be rebound to a confirmed transaction by `Scanner`, or add a verification API that takes the base key and the actual transaction/UTXO and checks the offset, script, value, txid, and vout before the object can be passed to `SignableTransaction::new`. [1](#0-0) [10](#0-9) 

### Proof of Concept
```rust
use bitcoin::{Amount, OutPoint, TxOut, Txid};
use bitcoin::consensus::serialize;
use bitcoin::hashes::Hash;
use k256::Scalar;

let group_key = keys.group_key(); // public, even-Y tweaked key
let forged_script = p2tr_script_buf(group_key).unwrap();

let forged_txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: forged_script,
};
let forged_outpoint = OutPoint {
  txid: Txid::all_zeros(),
  vout: 0,
};

let mut bytes = Vec::new();
bytes.extend(Scalar::ZERO.to_bytes());
bytes.extend(serialize(&forged_txout));
bytes.extend(serialize(&forged_outpoint));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000);

let tx = SignableTransaction::new(
  vec![forged],
  &payments,
  Some(change_script),
  None,
  fee_per_vbyte,
).unwrap();

// The offset/script check succeeds even though the outpoint is fabricated.
assert!(tx.multisig(&keys).is_some());
```

`read` succeeds because it performs only field decoding, while `multisig` checks only the offset-to-script relationship and does not validate that `forged_outpoint` resolves to `forged_txout`. [11](#0-10) [12](#0-11)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L77-85)
```rust
/// Return the Taproot address payload for a public key.
///
/// If the key is odd, this will return None.
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L115-133)
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

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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
