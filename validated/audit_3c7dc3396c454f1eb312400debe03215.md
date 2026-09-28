### Title
`ReceivedOutput::read` accepts unverified `(offset, TxOut, outpoint)` triples, letting untrusted bytes inject phantom UTXOs into `SignableTransaction` — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to `LMPVaultRouter` trusting a caller-supplied "vault" contract to release WETH, `ReceivedOutput::read` deserializes a completely attacker-controlled `(offset, TxOut, outpoint)` triple and treats it as a spendable wallet input without verifying that the outpoint exists on-chain, that the claimed `TxOut` value matches the real UTXO, or that the offset was legitimately produced by `Scanner`. The only downstream validation (`SignableTransaction::multisig`) binds the offset to the claimed `script_pubkey`, which the attacker controls anyway — so value and outpoint remain fully unauthenticated.

### Finding Description
The router bug is a missing authenticity check on a user-supplied object that gates movement of funds. In `bitcoin-serai`, `ReceivedOutput` is the object that gates movement of multisig funds:

`ReceivedOutput::read` performs only syntactic decoding — it reads a scalar via `Secp256k1::read_F`, then consensus-decodes a `TxOut` and `OutPoint` — and returns them as a "spendable output" with no semantic verification: [1](#0-0) 

`Scanner::scan_transaction` is the legitimate producer of `ReceivedOutput`s, binding each offset to a script it derived internally: [2](#0-1) 

`SignableTransaction::new` then trusts `input.output.value` to compute `input_sat`, the fee, and the change amount, and trusts `input.outpoint` as the spend target: [3](#0-2) [4](#0-3) 

`SignableTransaction::multisig` checks only `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. Because the attacker supplies both the offset and the `TxOut`, they can pick any offset scalar whose tweaked key they *choose* to put in `script_pubkey` — the check passes trivially for any self-consistent attacker-crafted pair, and says nothing about whether `outpoint` refers to a real UTXO or whether `value` is honest: [5](#0-4) 

### Impact Explanation
An unprivileged party able to feed bytes to `ReceivedOutput::read` (explicitly in-scope as an untrusted-bytes entry point) can inject phantom inputs claiming arbitrary value. Consequences:

- `SignableTransaction::new` computes `input_sat`, fee, and change from the fabricated `TxOut.value`. An inflated value makes `NotEnoughFunds` pass and produces change outputs/fee math for money that doesn't exist.
- `TransactionSignMachine::sign` signs each input with `Prevouts::All(&self.tx.prevouts)`, committing every signer to the fabricated prevout amounts. Since BIP-341 sighash commits to prevout values, signatures over mismatched amounts are invalid on-chain, so every co-signed transaction in the batch is unbroadcastable — real UTXOs referenced by the (attacker-chosen or legitimately scanned) outpoints become stuck pending manual recovery.
- This yields "funds reported received that are not spendable" plus a signing-round abort that wastes the threshold signing session, matching the accepted impact classes.

### Likelihood Explanation
Reachable whenever serialized `ReceivedOutput`s cross a trust boundary (coordinator → processor/message propagation), which is the documented use of the `read`/`write` pair. Unlike the Solidity router there is no registry (`vault => bool`) equivalent: nothing distinguishes a `ReceivedOutput` produced by `Scanner` from one fabricated byte-for-byte by an attacker, since all three fields are attacker-malleable and self-consistent. Severity is Medium: it corrupts fund accounting and can freeze real funds, but cannot redirect funds to the attacker because the offset-to-script binding still constrains which key signs.

### Recommendation
Authenticate `ReceivedOutput`s at the deserialization/processing boundary, as the router needed a vault registry:

- Recompute the expected `script_pubkey` from the claimed offset at read/handling time (`p2tr_script_buf(keys.group_key() + G*offset) == output.script_pubkey`) rather than deferring a self-referential check to `multisig`.
- Verify `outpoint` resolves to a confirmed on-chain UTXO whose `value` and `script_pubkey` match the claimed `TxOut` before adding it to the scheduler's UTXO set, or tag `ReceivedOutput`s as produced only by `Scanner` and reject externally supplied ones in the spending path.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read
let offset = Scalar::ONE; // any scalar
let fake_out = TxOut {
  value: Amount::from_sat(1_000_000_000),              // fabricated, inflated value
  script_pubkey: p2tr_script_buf(GENERATOR * offset + key).unwrap(), // self-consistent
};
let fake_point = OutPoint::new(Txid::from_byte_array([0x11; 32]), 0); // attacker-chosen

// serialize(offset || fake_out || fake_point) -> ReceivedOutput::read accepts it
let ro = ReceivedOutput::read(&mut &bytes[..]).unwrap(); // no verification performed

// SignableTransaction::new trusts ro.output.value for input_sat/fee/change
let stx = SignableTransaction::new(vec![ro], &payments, change, None, fee_rate).unwrap();
// multisig() passes: p2tr_script_buf(key + G*offset) == fake_out.script_pubkey
// sign() commits all signers to Prevouts::All containing the fabricated value
// resulting transaction's signatures are invalid on-chain -> funds frozen
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L175-185)
```rust
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
    }
    res
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-234)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```
