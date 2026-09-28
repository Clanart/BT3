### Title
Untrusted serialized outputs can report spendable funds backed by nonexistent inputs - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary

`ReceivedOutput::read` accepts the key offset, `TxOut`, and `OutPoint` as three independently supplied fields without authenticating that the referenced transaction output exists or was produced by `Scanner`. [1](#0-0)  `Scanner::scan_transaction` normally creates that binding by deriving the outpoint from the containing transaction and selecting the output by script, but this binding is not preserved or verified during deserialization. [2](#0-1)  A crafted serialized object can therefore combine the wallet’s valid spend script and offset with an arbitrary amount and nonexistent outpoint. [3](#0-2) 

### Finding Description

`ReceivedOutput` represents a received spendable output and stores three semantically dependent values: the scalar needed to spend the script, the claimed output, and the claimed transaction location. [4](#0-3)  The deserializer reads all three values and returns them without comparing the output against a block or transaction, checking the outpoint, or authenticating the serialized record. [3](#0-2) 

`SignableTransaction::new` then trusts the decoded `output.value` as available input value and places the decoded `outpoint` into the unsigned transaction input. [5](#0-4)  During signing setup, `SignableTransaction::multisig` verifies only that `keys.offset(offset).group_key()` produces the supplied script; it does not verify that `outpoint` resolves to that output or to any confirmed UTXO. [6](#0-5) 

The transaction signer commits to the attacker-selected `prevouts` using `Prevouts::All` and signs each input sighash. [7](#0-6)  Consequently, a crafted record whose script matches the wallet can pass the library’s local checks and cause threshold signing of a transaction referencing a nonexistent or mismatched output. [8](#0-7) 

### Impact Explanation

An attacker who can supply serialized wallet outputs can make `ReceivedOutput::value()` report arbitrary funds and can make `SignableTransaction` treat those funds as spendable. [9](#0-8) [10](#0-9)  The result is inventory and transaction construction for funds that cannot be spent because the encoded `OutPoint` does not reference the claimed output. [11](#0-10) 

This can also induce the threshold group to produce signatures over sighashes committing to fabricated previous-output data. [12](#0-11)  The signatures remain bound to the constructed sighash, so this does not directly produce a transferable signature or recover a key share, but it incorrectly represents an invalid transaction as the wallet’s signed spend. [13](#0-12) 

### Likelihood Explanation

The attack requires only the ability to feed bytes to `ReceivedOutput::read`, knowledge of the wallet’s public address or script, and a caller that uses the resulting object as transaction input. [14](#0-13)  No private key, validator privilege, malicious peer assumption, or control over an existing UTXO is required. [3](#0-2) 

The attacker can satisfy the only signing-time consistency check by copying the wallet’s legitimate spend script into the fabricated `TxOut` and using the corresponding public offset, including zero for the base key. [8](#0-7)  Arbitrary value and outpoint fields then control both the wallet’s reported balance and the transaction’s input reference. [10](#0-9) 

### Recommendation

Do not treat `ReceivedOutput` as an authenticated claim merely because it deserializes successfully. [3](#0-2)  For untrusted input, resolve `outpoint` against confirmed chain data, compare the actual UTXO with the decoded `TxOut`, reject immature coinbase outputs, and then recompute the registered script and offset through `Scanner`. [15](#0-14)  If serialized `ReceivedOutput` values are intended only for trusted persistence, they should be authenticated or integrity-protected so forged records cannot enter `SignableTransaction::new`. [16](#0-15) 

### Proof of Concept

1. Obtain the wallet’s public Taproot script for its base key and use offset zero, or use a previously registered public offset. [17](#0-16) 
2. Construct serialized bytes consisting of that scalar, a `TxOut` containing a large attacker-chosen amount and the wallet script, and an `OutPoint` naming a nonexistent transaction output. [18](#0-17) 
3. Pass the bytes to `ReceivedOutput::read`; the record is accepted because the fields are parsed independently and no chain lookup is performed. [3](#0-2) 
4. Pass the decoded record to `SignableTransaction::new`; its fabricated value satisfies the funding calculation and its fabricated outpoint is copied into the transaction input. [5](#0-4) 
5. Call `multisig`; it returns a machine because the offset derives the same script, even though the input does not exist. [6](#0-5) 
6. Run the normal preprocess/sign/complete flow; the resulting transaction has valid signatures for its fabricated sighash but cannot be accepted by Bitcoin because its referenced previous output is absent or inconsistent. [12](#0-11)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-227)
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
  }

  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
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

**File:** networks/bitcoin/src/crypto.rs (L59-72)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
```
