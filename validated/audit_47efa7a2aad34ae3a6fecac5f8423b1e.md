### Title
Unvalidated `ReceivedOutput` offset lets forged inputs be reported as spendable wallet funds - ([File: networks/bitcoin/src/wallet/mod.rs](https://github.com/Annirich/serai--013/blob/master/networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` accepts an arbitrary scalar offset, `TxOut`, and outpoint without proving that the output came from scanner registration or that the outpoint exists on-chain. [1](#0-0) 

A legitimately discovered output is bound to a transaction outpoint by `Scanner::scan_transaction`, which derives the outpoint from the actual transaction containing a registered script. [2](#0-1) 

### Finding Description
The `ReceivedOutput` serialization format contains three independent attacker-controlled fields: `offset`, `output`, and `outpoint`. [3](#0-2) 

`ReceivedOutput::read` only checks that the scalar decodes canonically and that the `TxOut`/`OutPoint` are syntactically valid; it does not validate that the outpoint refers to a transaction output matching `output` or that the output was discovered by `Scanner`. [4](#0-3) 

Later, `SignableTransaction::multisig` only verifies that `offset` transforms the threshold group key into the claimed `prevouts[i].script_pubkey`; it does not establish that the supplied outpoint exists or contains that output. [5](#0-4) 

### Impact Explanation
An unprivileged party who can cause untrusted bytes to be passed to `ReceivedOutput::read` can fabricate a Bitcoin input that appears spendable by the threshold wallet. By choosing the zero offset and a Taproot script for the wallet’s group key, the fabricated object passes the transaction machine’s script check while naming a nonexistent or unrelated outpoint. [4](#0-3) [6](#0-5) 

This can cause funds to be reported as received even though they are not spendable, and can cause the wallet to construct and sign a transaction spending a nonexistent input. The resulting transaction will be rejected by Bitcoin consensus, but only after the forged output has entered the wallet/signing path.

### Likelihood Explanation
The issue requires an input path where an attacker controls the serialized `ReceivedOutput`, such as imported wallet data, an untrusted backup/synchronization message, or another boundary explicitly feeding attacker bytes into `ReceivedOutput::read`. It does not affect outputs obtained solely through `Scanner::scan_transaction`, because that path derives the outpoint from the transaction being scanned. [7](#0-6) 

No key compromise, malicious validator, colluding threshold, or invalid curve encoding is required.

### Recommendation
Treat `ReceivedOutput` deserialization as validation-only for untrusted data:

- Do not call `ReceivedOutput::read` directly on attacker-controlled bytes and then treat the result as scanned wallet funds.
- Re-scan the referenced transaction/outpoint and compare the consensus-decoded `TxOut` with the stored `output`.
- Recompute or securely persist the scanner association between `script_pubkey` and `offset`.
- Reject serialized outputs whose `outpoint` cannot be resolved to an existing confirmed transaction.
- Consider storing only the authenticated outpoint plus scanner offset metadata, then reconstructing `TxOut` from chain data.

### Proof of Concept
Conceptually, for a wallet group key `K` whose even-y Taproot script is `p2tr_script_buf(K)`:

1. Serialize `offset = Scalar::ZERO`.
2. Serialize `TxOut { value: large_amount, script_pubkey: p2tr_script_buf(K) }`.
3. Serialize an arbitrary nonexistent `OutPoint`.
4. Pass the bytes to `ReceivedOutput::read`.
5. Build a `SignableTransaction` from the resulting object and call `multisig(keys)`.

The script check succeeds because the zero offset leaves the group key unchanged, while the claimed `prevouts[i].script_pubkey` is the corresponding Taproot script. [8](#0-7) 

The fabricated output is therefore treated as wallet-controlled funding input even though the arbitrary outpoint may not exist. This violates the expected invariant that a `ReceivedOutput` represents an output discovered at a concrete transaction position. [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

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
