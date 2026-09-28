### Title
Untrusted `ReceivedOutput` deserialization forges unspendable Bitcoin deposits - (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts an attacker-controlled scalar offset, `TxOut`, and `OutPoint` without proving that the output exists, is confirmed, or was discovered by `Scanner`. A forged object can therefore report received funds and, when its script matches the wallet’s public P2TR script, can pass the later key-ownership check while referencing a nonexistent UTXO. [1](#0-0) 

### Finding Description
`ReceivedOutput` is intended to represent a spendable Bitcoin output and stores the key offset, claimed output, and claimed outpoint. [2](#0-1) 

The deserializer reads those three fields directly from the supplied byte stream and returns the object without any provenance or chain-state validation. [1](#0-0) 

By contrast, `Scanner` normally creates these objects only for transaction outputs whose `script_pubkey` is registered for the monitored key. [3](#0-2) 

`value()` reports the amount embedded in the attacker-supplied `TxOut`. [4](#0-3) 

When the forged output uses the wallet’s ordinary P2TR script and a zero offset, `SignableTransaction::multisig` accepts it because its ownership check only compares the derived P2TR script with the claimed previous output’s script. [5](#0-4) 

### Impact Explanation
An application that imports or accepts serialized `ReceivedOutput` values can report attacker-selected balances as received. Those reported funds are not spendable because the referenced `OutPoint` may be nonexistent, immature, already spent, or attached to an unrelated transaction. [1](#0-0) 

If the forged `TxOut` uses a wallet-controlled script, transaction construction and threshold signing can proceed even though the transaction will be invalid on Bitcoin due to the fabricated previous output. [6](#0-5) 

### Likelihood Explanation
The attacker only needs to supply bytes to `ReceivedOutput::read` and know the target wallet’s public P2TR script. No private key material is required to fabricate the object or the reported value. [7](#0-6) 

The impact is highest for software treating imported `ReceivedOutput` values as authoritative deposits; restricting the deserializer to authenticated local state would reduce exposure. [8](#0-7) 

### Recommendation
Bind deserialization to an expected `Scanner` or wallet key, recompute the expected script from `key + offset * G`, and reject mismatched outputs. Before treating a deserialized output as received, verify the outpoint against a confirmed Bitcoin block, verify its contained `TxOut` exactly matches, and enforce coinbase maturity. Alternatively, remove the public untrusted deserialization path or require authenticated provenance for stored scanner results.

### Proof of Concept
The following pseudocode constructs a forged object for the wallet’s ordinary key script, using a zero offset and an arbitrary nonexistent outpoint:

```rust
// wallet_script is publicly derivable from the wallet's even P2TR group key.
let wallet_script = p2tr_script_buf(keys.group_key()).unwrap();

let fake_output = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: wallet_script,
};

let fake_outpoint: OutPoint = /* any syntactically valid, nonexistent outpoint */;

let mut bytes = Scalar::ZERO.to_bytes().to_vec();
bytes.extend(serialize(&fake_output));
bytes.extend(serialize(&fake_outpoint));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 100_000);

// This succeeds because the script matches the key derived with offset zero.
let signable = SignableTransaction::new(
  vec![forged],
  payments,
  change,
  None,
  fee_per_vbyte,
).unwrap();
assert!(signable.multisig(&keys).is_some());
```

The resulting transaction can proceed to threshold signing, but Bitcoin consensus rejects it because its claimed previous output does not exist or is not spendable. [6](#0-5)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L273-284)
```rust

```
