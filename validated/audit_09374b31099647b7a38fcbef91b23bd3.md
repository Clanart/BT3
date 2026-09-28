### Title
Forged `ReceivedOutput` records are accepted as spendable wallet inputs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an attacker-controlled scalar offset, claimed `TxOut`, and claimed `OutPoint` without proving that the output exists on-chain or that the claimed amount belongs to that outpoint. `SignableTransaction::multisig` then authenticates the entry only by comparing `prevouts[i].script_pubkey` to the key derived from `keys.offset(offset)`, leaving the transaction ID, vout, and value unauthenticated. A party who knows a wallet's Taproot address can submit `offset = 0`, a matching script, an arbitrary amount, and a nonexistent or unrelated outpoint, causing the wallet to treat nonexistent funds as spendable and sign a transaction that Bitcoin consensus rejects.

### Finding Description
`ReceivedOutput::read` parses three independent fields from untrusted bytes: a secp256k1 scalar offset, a consensus-encoded `TxOut`, and a consensus-encoded `OutPoint` [1](#0-0) . There is no check that `outpoint` identifies a confirmed transaction output whose contents equal `output`.

When creating a signing machine, the code checks only that `p2tr_script_buf(keys.offset(offset).group_key())` equals the supplied `prevouts[i].script_pubkey` [2](#0-1) . It does not check the claimed `outpoint`, amount, or existence of the output. For the wallet's base Taproot address, the attacker can use offset zero, because offsetting by zero leaves the wallet group key unchanged.

The resulting transaction is signed with `Prevouts::All(&self.tx.prevouts)`, so the forged amount and script are committed into every input's Taproot sighash [3](#0-2) . If the claimed `OutPoint` does not exist, or refers to an output with different contents, the signature does not authorize spending a real wallet output and the transaction is invalid.

### Impact Explanation
An unprivileged party can cause the wallet to account for attacker-supplied bytes as a spendable `ReceivedOutput` even though no corresponding spendable Bitcoin output exists. Any balance, deposit, or payment flow that trusts the deserialized record can credit funds that cannot actually be spent. It can also cause the threshold wallet to generate a signature for a transaction whose input commitment is false, resulting in an invalid transaction and disruption of withdrawal or consolidation flows.

This is the Serai-shaped analog of the injection issue: attacker-controlled bytes are interpreted as a trusted, semantically meaningful object rather than remaining raw data. The scalar and transaction fields are accepted as authoritative wallet state without an authenticity invariant tying them to an observed chain output.

### Likelihood Explanation
The attacker only needs a target wallet's known Taproot script and a path that feeds attacker-controlled serialized `ReceivedOutput` data into `ReceivedOutput::read`. Constructing the forged object is straightforward: serialize scalar zero, a `TxOut` containing the wallet's script and a chosen amount, and any `OutPoint`. No discrete-log knowledge, signature forgery, validator compromise, or malformed curve encoding is required.

The issue is bounded by the caller accepting untrusted serialized wallet outputs. If this API is used only for trusted local state created by `Scanner::scan_transaction`, the forged-object path is not exposed to a network attacker [4](#0-3) .

### Recommendation
Treat `ReceivedOutput` as untrusted input rather than authoritative wallet state. After deserialization, resolve `outpoint` against a trusted Bitcoin view and require the returned `TxOut` to equal `self.output`, including both value and `script_pubkey`. Alternatively, make `ReceivedOutput::read` crate-private or explicitly restrict it to authenticated local storage, and provide a separate untrusted-input constructor that performs on-chain validation.

`SignableTransaction::multisig` should not rely solely on `script_pubkey` matching. It should ensure each `ReceivedOutput` was produced by an authenticated scanner result or by successful chain lookup before creating `AlgorithmMachine` instances [5](#0-4) .

### Proof of Concept
Conceptually, for a wallet with base Taproot script `S`:

```rust
let forged = ReceivedOutput {
  offset: Scalar::ZERO,
  output: TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: S.clone(),
  },
  outpoint: OutPoint::new(nonexistent_txid, 0),
};
```

Serialize this as `offset || TxOut || OutPoint` and pass the bytes to `ReceivedOutput::read`. Because `Scalar::ZERO` preserves the base group key and the supplied `script_pubkey` is `S`, the `multisig` check succeeds [6](#0-5) . `SignableTransaction::new` then stores the attacker-claimed output in `prevouts` and builds an input referencing the fake `outpoint` [7](#0-6) . Signing commits to the forged prevout through `Prevouts::All`, producing a transaction that cannot spend a real output [8](#0-7) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L99-133)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
