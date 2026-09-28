### Title
Deserialized Bitcoin outputs bypass `Scanner` validation and can report unspendable funds - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
Medium. `ReceivedOutput::read` accepts an arbitrary scalar offset, transaction output, and outpoint without verifying that the output was discovered by `Scanner`, that its `script_pubkey` corresponds to `base_key + offset * G`, or that the referenced transaction output exists. [1](#0-0)  In contrast, `Scanner::scan_transaction` only creates a `ReceivedOutput` after the transaction's actual `script_pubkey` matches a registered scanner script and derives its outpoint from the containing transaction. [2](#0-1) 

### Finding Description
`Scanner` establishes the security invariant that a received output is spendable by the tracked key under the returned offset. `Scanner::new` registers the P2TR script for the base key, `register_offset` maps derived scripts to offsets, and `scan_transaction` only constructs `ReceivedOutput` after matching an on-chain output's `script_pubkey`. [3](#0-2) 

`ReceivedOutput::read` provides an alternate construction path that omits that policy entirely. It deserializes the fields independently and immediately returns `Ok(ReceivedOutput { offset, output, outpoint })`. [1](#0-0)  An unprivileged caller supplying these bytes can therefore mint a `ReceivedOutput` claiming any value, script, offset, transaction ID, and output index.

The later signing path does check that `keys.offset(offset).group_key()` produces the previous output's script before creating the transaction machine, so this does not prove that an unrelated output can be signed. [4](#0-3)  The bypass is in the receipt/accounting boundary: a deserialized object presents itself as “a spendable output” without satisfying the scanner-derived invariant.

### Impact Explanation
An attacker who can feed serialized `ReceivedOutput` bytes to an application can cause it to report a deposit of arbitrary Bitcoin value for an arbitrary outpoint, including an output that never existed or is not spendable by the expected key. This matches the accepted impact of funds reported as received when they are not actually spendable.

The object can also enter `SignableTransaction::new`, where its claimed value is included in input accounting before later key/script validation occurs. [5](#0-4) 

### Likelihood Explanation
The attack requires an application to treat `ReceivedOutput::read` input as a valid scanner result, such as when accepting a serialized output from an untrusted source rather than only reconstructing trusted local state. No private key, validator privilege, malformed curve encoding, or on-chain cooperation is required: all supplied fields are public bytes.

Severity is Medium rather than High because `SignableTransaction::multisig` rejects an offset/script mismatch before producing a signature machine. [6](#0-5)  The demonstrated impact is false receipt and balance reporting, not arbitrary transaction signing.

### Recommendation
Do not expose `ReceivedOutput::read` as an unauthenticated constructor for spendable outputs. Replace it with a key-aware or transaction-aware API that verifies:

1. `output.script_pubkey == p2tr_script_buf(expected_key + G * offset)`;
2. `outpoint` identifies an actual output in the supplied transaction or a verified chain object;
3. the output at that index equals the deserialized `TxOut`.

If deserialization is intended only for trusted storage, rename or document it accordingly and require callers to revalidate restored objects against `Scanner` and the containing transaction before accounting or spending them.

### Proof of Concept
The following byte sequence creates a `ReceivedOutput` claiming a 1,000,000-satoshi output at an arbitrary outpoint without invoking `Scanner`:

```rust
use bitcoin::{
  Amount, OutPoint, ScriptBuf, TxOut, Txid,
  consensus::{Encodable, serialize},
  hashes::Hash,
};
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;

let mut bytes = Vec::new();

// Arbitrary scalar offset accepted by Secp256k1::read_F.
bytes.extend(Scalar::ONE.to_bytes());

// Arbitrary TxOut accepted by consensus_decode.
let fake_output = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: ScriptBuf::new(), // No matching Scanner script is required.
};
bytes.extend(serialize(&fake_output));

// Arbitrary txid/vout; no containing transaction is checked.
let fake_outpoint = OutPoint {
  txid: Txid::from_byte_array([0x42; 32]),
  vout: 0,
};
fake_outpoint.consensus_encode(&mut bytes).unwrap();

let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(received.value(), 1_000_000);
assert_eq!(received.outpoint(), &fake_outpoint);
```

No Bitcoin transaction containing `fake_outpoint` and no scanner registration for the script are needed for `read` to return successfully.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L158-195)
```rust
impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }

  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
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
    }
    res
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

**File:** networks/bitcoin/src/wallet/send.rs (L270-279)
```rust
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
```
