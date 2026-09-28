### Title
`ReceivedOutput::read` accepts an offset/script mismatch, yielding reported funds that cannot be spent - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` deserializes the spending offset and `TxOut` independently, without proving that the output’s P2TR script is the key obtained by applying that offset to the wallet key. `value()` then exposes the claimed amount, while `SignableTransaction::multisig` later rejects the same inconsistent offset/script pair because the offset-derived key does not match the prevout script. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
A `ReceivedOutput` claims that `offset` turns the wallet’s base key into the key committed by `output.script_pubkey`, but `ReceivedOutput::read` only parses `offset`, `output`, and `outpoint` in sequence and never checks that relationship or even that the output is P2TR. [4](#0-3) [1](#0-0) 

The only production construction path shown here establishes that relationship by looking up the script in `Scanner::scripts` and storing the associated offset. [5](#0-4) [6](#0-5) 

### Impact Explanation
If attacker-controlled or corrupted serialized bytes are passed to `ReceivedOutput::read`, the parser returns an apparently spendable output and reports its full `TxOut` value even though the supplied offset does not derive that output’s key. [7](#0-6) [1](#0-0) 

When spending is attempted, `SignableTransaction::multisig` applies the stored offset to the threshold key and compares the resulting P2TR script with the prevout script, returning `None` on the forged association. [3](#0-2) 

This produces funds reported as received that are not spendable by the wallet, matching the relevant impact class. [4](#0-3) [3](#0-2) 

### Likelihood Explanation
The precondition is an attacker supplying serialized `ReceivedOutput` bytes to a caller that persists, relays, or otherwise trusts those bytes; ordinary blockchain scanning is not affected because `Scanner::scan_transaction` creates the script/offset association from its registered map. [6](#0-5) 

Changing the serialized scalar to any value other than a scalar yielding the same x-only output key is sufficient, so no cryptographic hardness assumption needs to be broken. [1](#0-0) [3](#0-2) 

### Recommendation
Make deserialization context-dependent: require the wallet base key or scanner registry and reject the object unless `p2tr_script_buf(base_key + offset * G) == output.script_pubkey`. [8](#0-7) [1](#0-0) 

Alternatively, remove the standalone public deserialization constructor and only reconstruct `ReceivedOutput` values through a scanner or an authenticated serialization layer. [9](#0-8) [6](#0-5) 

### Proof of Concept
```rust
use bitcoin::{consensus::encode::serialize_hex, OutPoint, ScriptBuf, TxOut};
use bitcoin::hashes::Hash;
use bitcoin::transaction::Amount;
use bitcoin::Txid;
use k256::Scalar;
use networks_bitcoin::wallet::ReceivedOutput;

// Serialize:
//   scalar = Scalar::ONE
//   output = an arbitrary P2TR TxOut whose script is unrelated to wallet_key + G
//   outpoint = an arbitrary outpoint
let mut bytes = Scalar::ONE.to_bytes().to_vec();
bytes.extend(serialize_hex(&TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: unrelated_p2tr_script,
}).into_bytes()?);
bytes.extend(serialize_hex(&OutPoint::new(Txid::all_zeros(), 0)).into_bytes()?);

let reported = ReceivedOutput::read(&mut bytes.as_slice())?;
assert_eq!(reported.value(), 100_000);

// The parser accepted the object despite there being no proof that
// `Scalar::ONE` is the output's spending offset.
```

`ReceivedOutput::read` accepts this tuple because it performs no offset-to-script consistency check, while `value()` returns `100_000`. [10](#0-9) 

Any later attempt to spend through `SignableTransaction::multisig` compares `p2tr_script_buf(keys.offset(Scalar::ONE).group_key())` with the unrelated prevout script and returns `None`. [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L77-86)
```rust
/// Return the Taproot address payload for a public key.
///
/// If the key is odd, this will return None.
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
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

**File:** networks/bitcoin/src/wallet/mod.rs (L99-134)
```rust
impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

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

**File:** networks/bitcoin/src/wallet/mod.rs (L151-165)
```rust
/// A transaction scanner capable of being used with HDKD schemes.
#[derive(Clone, Debug)]
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
