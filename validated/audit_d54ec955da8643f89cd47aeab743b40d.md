### Title
Forged `ReceivedOutput` deserialization reports unspendable Bitcoin funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts an attacker-supplied scalar offset, complete claimed `TxOut`, and claimed `OutPoint` as independent fields, without proving that the outpoint resolves to the claimed output. [1](#0-0) 

This differs from the safe construction used by `Scanner::scan_transaction`, which creates the record only after matching an actual transaction output and derives the outpoint from that same transaction. [2](#0-1) 

Severity: Medium.

### Finding Description
`ReceivedOutput` represents a spendable output and exposes the attacker-controlled claimed `TxOut`, outpoint, offset, and value through its accessors. [3](#0-2) 

The deserializer only checks that the scalar is canonical and that the `TxOut` and `OutPoint` are syntactically decodable; it does not require any relationship between the declared output and the referenced UTXO. [1](#0-0) 

An unprivileged party can therefore supply bytes naming a real unrelated UTXO—or a nonexistent outpoint—while embedding a different `TxOut` carrying a Serai-controlled P2TR script and arbitrary amount. [4](#0-3) 

### Impact Explanation
The resulting object reports funds under a Serai-controlled script even though the referenced Bitcoin outpoint does not contain those funds or may not exist. [5](#0-4) 

A consumer that treats deserialized `ReceivedOutput` records as authenticated scanner results can therefore mark phantom deposits as received and later build transactions that are unspendable or fail during broadcast. [2](#0-1) 

### Likelihood Explanation
The attack requires only attacker-controlled bytes passed to `ReceivedOutput::read`, one of the accepted public-input surfaces for this audit. [1](#0-0) 

No cryptography needs to be broken: the attacker selects the scalar, claimed output, amount, script, transaction ID, and vout directly. [6](#0-5) 

### Recommendation
Do not expose `ReceivedOutput::read` as evidence that an output exists or is spendable. [1](#0-0) 

Construct `ReceivedOutput` exclusively from a scanned transaction, or extend deserialization/validation to resolve the claimed `OutPoint` and compare the actual prevout’s `TxOut` with the embedded `TxOut`. [2](#0-1) 

If persistent serialization is required, bind the output data to the outpoint cryptographically and reject records whose actual chain output differs. [7](#0-6) 

### Proof of Concept
The following constructs an accepted `ReceivedOutput` whose claimed output pays `victim_key`, while its outpoint names a different or nonexistent UTXO. [8](#0-7) 

```rust
use bitcoin_serai::{
  bitcoin::{
    consensus::Encodable,
    transaction::OutPoint,
    Amount, TxOut,
  },
  wallet::{p2tr_script_buf, ReceivedOutput},
};
use k256::Scalar;

fn forged_output(
  victim_key: k256::ProjectivePoint,
  unrelated_txid: bitcoin_serai::bitcoin::Txid,
) -> ReceivedOutput {
  let script = p2tr_script_buf(victim_key).unwrap();

  let mut bytes = Vec::new();

  // Claim the output uses the un-offset victim key.
  bytes.extend_from_slice(&Scalar::ZERO.to_bytes());

  // Claim the prevout contains this output and amount.
  TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: script,
  }
  .consensus_encode(&mut bytes)
  .unwrap();

  // Point at an unrelated transaction output. Nothing checks that this
  // prevout actually has the above script or value.
  OutPoint { txid: unrelated_txid, vout: 0 }
    .consensus_encode(&mut bytes)
    .unwrap();

  let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
  assert_eq!(forged.value(), 1_000_000);
  assert_eq!(forged.offset(), Scalar::ZERO);
  forged
}
```

`Scanner` would never produce this record unless the actual transaction output matched the registered P2TR script, but the standalone deserializer accepts the forged record because the fields are not cryptographically or semantically bound. [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L88-118)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-141)
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

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
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
