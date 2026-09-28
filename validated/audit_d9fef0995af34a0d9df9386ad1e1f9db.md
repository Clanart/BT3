### Title

Unauthenticated offset in `ReceivedOutput::read` makes attacker-supplied funds appear spendable but unusable - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary

`ReceivedOutput::read` accepts a serialized scalar offset without proving that the offset corresponds to the serialized Taproot `script_pubkey`. [1](#0-0)  An attacker can therefore claim that a real output is controlled by a different derived key, analogous to supplying the expected `_creator` argument rather than proving control of the caller identity.

### Finding Description

`ReceivedOutput` stores the scalar used to derive the spendable key, the output, and its outpoint. [2](#0-1)  Its deserializer independently reads the scalar and output, then constructs `ReceivedOutput` without any consistency check. [3](#0-2)  In contrast, genuinely scanned outputs obtain their offset from the registered `script_pubkey`, which cryptographically binds the two values. [4](#0-3) 

`SignableTransaction::new` trusts this unauthenticated offset when accounting for input value and constructing inputs. [5](#0-4)  Only later does `multisig` derive `keys.offset(offset).group_key()` and reject the input when that derived key does not match `prevouts[i].script_pubkey`. [6](#0-5) 

### Impact Explanation

An attacker who can feed serialized wallet state into `ReceivedOutput::read` can make an unrelated or incorrectly offset Bitcoin output report its full value through `value()`. [7](#0-6)  The object can then be accepted by `SignableTransaction::new`, but `multisig` returns `None`, so the reported funds cannot actually be signed or spent under the wallet’s threshold keys. [6](#0-5)  This is a Medium-severity integrity failure because it can cause downstream balance/accounting logic to treat uns spendable funds as available.

### Likelihood Explanation

The attack only requires control of bytes passed to the explicitly exposed `ReceivedOutput::read` API; no validator privilege, private key, signature, or collusion is needed. [3](#0-2)  Exploitation requires an integration boundary where these serialized records are attacker-controlled rather than generated solely by `Scanner::scan_transaction`. [4](#0-3) 

### Recommendation

Replace the unauthenticated constructor path with a key-bound deserializer, such as `ReceivedOutput::read_for_key(reader, base_key)`, which verifies that `p2tr_script_buf(base_key + GENERATOR * offset) == output.script_pubkey` before returning `Ok`. [8](#0-7)  Persist or transmit the base key context needed for this check, and reject any record whose claimed offset does not derive the serialized output’s Taproot script.

### Proof of Concept

```rust
// Conceptual reproduction:
let real = scanner.scan_transaction(&tx).remove(0);
let mut bytes = real.serialize();

// Replace the claimed spend offset with a different scalar.
bytes[.. 32].copy_from_slice(&Scalar::ONE.to_bytes());

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), real.value());
assert_eq!(forged.output(), real.output());
assert_ne!(forged.offset(), real.offset());

// The forged record is still counted as spendable input value.
let signable = SignableTransaction::new(
  vec![forged],
  &payments,
  change,
  data,
  fee_per_vbyte,
).unwrap();

// But signing rejects it because offset=1 derives a different script_pubkey.
assert!(signable.multisig(&keys).is_none());
```

The deserializer accepts the attacker-declared offset and output independently, while `multisig` performs the missing relationship check only after balance and transaction construction have already consumed the forged record. [3](#0-2) [6](#0-5)

### Citations

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
