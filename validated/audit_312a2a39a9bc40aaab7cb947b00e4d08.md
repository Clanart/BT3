### Title
Unauthenticated `ReceivedOutput` deserialization accepts unverified UTXO ownership and value - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and `OutPoint` without proving that the referenced Bitcoin output exists, belongs to the wallet's base key, or corresponds to a registered scanner offset. An attacker who can provide bytes to this deserializer can fabricate a received output or alter the value/outpoint associated with a valid script. [1](#0-0) 

### Finding Description
`ReceivedOutput` combines the spend offset, claimed prevout contents, and claimed outpoint. [2](#0-1) 

Legitimate construction is gated by `Scanner`: `Scanner::new` associates the base-key script with offset zero, `register_offset` computes a script for `key + offset * G`, and `scan_transaction` only emits a `ReceivedOutput` when the on-chain `script_pubkey` is present in that authenticated mapping. [3](#0-2) [4](#0-3) [5](#0-4) 

The deserializer bypasses that boundary entirely. It decodes the three attacker-controlled fields independently and returns `Ok(ReceivedOutput)` without checking the script against a base key, checking that the offset was registered, or checking that the outpoint resolves to the claimed `TxOut`. [6](#0-5) 

The resulting object then behaves as a claimed spendable input: `SignableTransaction::new` sums the encoded `TxOut` value as available input funds, uses the encoded offset, and places the encoded outpoint into the transaction input. [7](#0-6) 

Although `SignableTransaction::multisig` later checks that `base_key + offset * G` produces the encoded `script_pubkey`, it does not verify that the encoded `outpoint` exists or actually contains the encoded `TxOut`. [8](#0-7) 

### Impact Explanation
An attacker can submit serialized bytes describing a syntactically valid `ReceivedOutput` for a wallet script while inventing its `OutPoint` or inflating its `TxOut.value`. If those bytes are consumed as wallet scan/state data, the wallet reports funds as received even though no corresponding spendable UTXO exists. [9](#0-8) [7](#0-6) 

For a correctly crafted script/offset pair, `multisig` accepts the forged object and creates signing machines for a transaction spending the nonexistent or misrepresented outpoint. The resulting threshold signature can be valid over the transaction's sighash while the transaction itself is unspendable on-chain because the claimed prevout or amount is false. [10](#0-9) [11](#0-10) 

This maps to the report's missing-authorization class as an ownership/authorization failure: the scan path authorizes outputs by membership in `Scanner::scripts`, but deserialization reconstructs the same “received output” claim without that authorization. [12](#0-11) [13](#0-12) 

### Likelihood Explanation
The prerequisite is an integration that persists, relays, or otherwise accepts serialized `ReceivedOutput` values from an untrusted source and later treats them as discovered wallet inputs. The vulnerability does not require secret keys, validator privileges, malformed curve encodings, or control of the Bitcoin network; all fields needed by the forged object are public serialization bytes. [6](#0-5) 

The attack can be made deterministic: choosing offset zero and a `TxOut` whose `script_pubkey` equals the wallet's base-key P2TR script passes the later key-binding check while still allowing a fabricated outpoint and amount. [14](#0-13) [15](#0-14) 

### Recommendation
Do not allow `ReceivedOutput::read` to reconstruct wallet ownership claims without context. At minimum:

- Require a base key or scanner context when deserializing and reject unless `p2tr_script_buf(base_key + offset * G) == output.script_pubkey`.
- Prefer a `Scanner`-associated constructor that only deserializes offsets/scripts already present in `scripts`.
- Before treating a decoded output as available balance, verify the encoded `outpoint` resolves on-chain to the exact encoded `TxOut`.
- Keep the script/offset validation even in `SignableTransaction::multisig`, but treat it as a defense-in-depth check rather than the deserialization boundary. [4](#0-3) [15](#0-14) 

### Proof of Concept
The following deterministic construction demonstrates the bypass. For a wallet base key `key`, use offset zero, the legitimate `p2tr_script_buf(key)` script, an attacker-chosen nonzero amount, and an arbitrary/nonexistent `OutPoint`:

```rust
// From networks/bitcoin/src/wallet/mod.rs and send.rs APIs.
let mut bytes = Vec::new();

// offset = 0
bytes.extend_from_slice(Scalar::ZERO.to_bytes().as_ref());

// Claimed prevout contents: valid wallet script, forged amount.
let output = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: p2tr_script_buf(key).unwrap(),
};
bytes.extend_from_slice(&bitcoin::consensus::serialize(&output));

// Claimed nonexistent input.
let outpoint = OutPoint::null();
bytes.extend_from_slice(&bitcoin::consensus::serialize(&outpoint));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000);

let signable = SignableTransaction::new(
  vec![forged],
  &[(p2tr_script_buf(key).unwrap(), 1_000)],
  Some(p2tr_script_buf(key).unwrap()),
  None,
  20,
).unwrap();

// This passes the script/offset binding even though the outpoint is fabricated.
assert!(signable.multisig(&threshold_keys).is_some());
```

`ReceivedOutput::read` returns the forged object because it performs no ownership or ledger validation. [6](#0-5)  `SignableTransaction::multisig` then accepts the offset-zero wallet script because the key-derived script matches the encoded `script_pubkey`. [16](#0-15)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-85)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L154-165)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L185-191)
```rust
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
