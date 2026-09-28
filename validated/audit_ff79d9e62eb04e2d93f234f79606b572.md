### Title
Untrusted `ReceivedOutput` bytes can report nonexistent or uncontrollable Bitcoin funds - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and `OutPoint` without proving the output exists on-chain or that its `script_pubkey` corresponds to the claimed offset. Because `ReceivedOutput` represents a spendable output, crafted bytes can cause Serai to report funds that cannot actually be spent. [1](#0-0) 

### Finding Description
`ReceivedOutput` contains the purported offset, full `TxOut`, and `OutPoint`. Its parser only performs scalar and consensus decoding, then directly constructs the object. [2](#0-1) 

The legitimate constructor path is `Scanner::scan_transaction`, which only creates a `ReceivedOutput` when the transaction output's `script_pubkey` is present in the scanner's registered script-to-offset map. [3](#0-2)  Deserialization bypasses this invariant and supplies all fields independently.

The issue becomes concrete during transaction construction and signing. `SignableTransaction::new` trusts the supplied `ReceivedOutput` for the input's previous outpoint and for the `TxOut` used both in accounting and in the Taproot `Prevouts::All` commitment. [4](#0-3) [5](#0-4) 

`SignableTransaction::multisig` only verifies that the supplied output's `script_pubkey` equals `p2tr_script_buf(keys.offset(offset).group_key())`. It does not verify that the referenced outpoint exists or that the claimed amount matches the blockchain output. [6](#0-5)  An attacker can therefore use a nonexistent `OutPoint` and an inflated `TxOut` amount while selecting the scanner-expected script for an offset such as zero.

### Impact Explanation
A crafted serialized `ReceivedOutput` can report an arbitrary Bitcoin output as received. If the fake `TxOut` uses the expected P2TR script for the claimed offset, `SignableTransaction::multisig` accepts it and the FROST signing machines produce signatures over `Prevouts::All` committing to the forged amount. [6](#0-5) [5](#0-4) 

Bitcoin consensus will reject the resulting transaction if the outpoint does not exist or the serialized amount exceeds the actual UTXO amount. The result is funds reported as received which are not actually spendable, and potentially an incorrect balance/accounting state based on attacker-supplied serialized wallet data.

### Likelihood Explanation
The primitive is reachable through the explicitly exposed `ReceivedOutput::read` API and requires only byte-level control. The attacker does not need a validator key share, malformed curve encoding, or consensus-invalid signature. For a scanner-registered offset, deriving the required `script_pubkey` is public because `p2tr_script_buf` maps the offset-adjusted group key to the Taproot output script. [7](#0-6) 

### Recommendation
Do not treat deserialization as a trusted `ReceivedOutput` constructor. At minimum:

- Require the base group key when deserializing and verify `p2tr_script_buf(key + G * offset) == output.script_pubkey`.
- Before treating the object as spendable, resolve the `OutPoint` against a trusted Bitcoin node and verify the returned `TxOut` exactly equals the stored amount and script.
- Prefer making this parser crate-private or clearly trusted-only, and expose scanner-derived outputs as the supported untrusted-input path.
- Persist the scanner's output identity/invariant rather than allowing independent attacker-controlled `offset`, `TxOut`, and `OutPoint` fields.

### Proof of Concept
Conceptually, serialize the following fields in the order consumed by `ReceivedOutput::read`:

1. `offset`: canonical encoding of `Scalar::ZERO`.
2. `output`: a consensus-encoded `TxOut` with:
   - `value`: an arbitrary inflated amount.
   - `script_pubkey`: `p2tr_script_buf(group_key)` for the wallet's external deposit key.
3. `outpoint`: a consensus-encoded `OutPoint` naming a nonexistent transaction or a transaction whose real output has a lower amount.

`ReceivedOutput::read` succeeds because it performs no semantic validation. `SignableTransaction::new` then uses the fake `TxOut` as the committed prevout, while `SignableTransaction::multisig` accepts the crafted input because the fake script matches the zero-offset wallet key. Signing produces a transaction whose signatures commit to the fabricated amount, but Bitcoin rejects it because the referenced UTXO or amount does not exist.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-134)
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-195)
```rust
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
