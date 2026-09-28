### Title
`ReceivedOutput::read` accepts an offset and `TxOut` without verifying the offset's derived key can actually spend the output — funds reported received that are not spendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Scanner establishes the critical invariant that an output's `script_pubkey` equals the P2TR script for `key + G*offset` before producing a `ReceivedOutput`. `ReceivedOutput::read` / `ReceivedOutput::write` bypass this entirely: untrusted bytes are deserialized into an `offset`, an arbitrary `TxOut`, and an `OutPoint` with no consistency check. This is a fail-open analog to the missing-permission bug: the constructor that enforces the authorization invariant (`Scanner::scan_transaction`) is skipped by the deserialization path, which performs no validation at all.

### Finding Description
`Scanner::scan_transaction` only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns an offset — i.e., the output is provably spendable by `key + G*offset` (modulo the parity bump inside `register_offset`).

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput {
    offset: *offset,
    output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout),
  });
}
``` [1](#0-0) 

`ReceivedOutput::read` reconstructs the same struct from raw bytes with no such check:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output;
  let outpoint;
  {
    let mut buf_r = BufReader::with_capacity(0, r);
    output = TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
    outpoint = OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
  }
  Ok(ReceivedOutput { offset, output, outpoint })
}
``` [2](#0-1) 

There is no verification that `p2tr_script_buf(key + G*offset) == Some(output.script_pubkey)`. `offset` is a fully attacker-controlled scalar and `output` is a fully attacker-controlled `TxOut`. The writer/reader pair (`serialize`/`read`) is the transport format for carrying scanned outputs between components, so these bytes cross a trust boundary.

### Impact Explanation
A `ReceivedOutput` is the wallet's representation of a spendable, received coin. A party able to supply serialized `ReceivedOutput`s (e.g., a coordinator or peer feeding scan results to an offline/signing component) can cause outputs to be reported as received that the key cannot spend: either the `script_pubkey` doesn't match `key + G*offset` (the signature will be for the wrong key — the input is unspendable and, if the wallet funds a transaction with it, fee/change value is burned), or the `offset` matches but the `TxOut`/`OutPoint` references a nonexistent or immature coinbase output. This satisfies the "funds reported received that are not spendable" impact class.

### Likelihood Explanation
Reachability requires untrusted bytes reaching `ReceivedOutput::read`. The scanner itself runs against the Bitcoin chain, but `serialize`/`read` exist precisely to move results across process boundaries; any component trusting serialized `ReceivedOutput`s from another party inherits the discrepancy. The attacker needs no key material — only the ability to supply bytes. Severity is bounded by whether downstream `send` logic re-derives the script from `offset` before signing; if it does, impact degrades to a signing failure rather than burned funds.

### Recommendation
Either bind `ReceivedOutput` construction to a checked constructor — add a `verify(key)`/`new(key, offset, output, outpoint)` that asserts `p2tr_script_buf(key + G*offset) == Some(output.script_pubkey)` — or require `read` to be followed by such a validation call before the output is treated as received/spendable. Document that the serialized form is unauthenticated.

### Proof of Concept
```rust
// Attacker-controlled bytes: offset = 1, output paying to an unrelated P2TR key
let attacker_scalar = Scalar::ONE;
let unrelated_script = ScriptBuf::new_p2tr_tweaked(
  TweakedPublicKey::dangerous_assume_tweaked(x_only(&some_other_point)),
);
let tx_out = TxOut { value: Amount::from_sat(100_000), script_pubkey: unrelated_script };
let outpoint = OutPoint::new(attacker_txid, 0);

let mut bytes = attacker_scalar.to_bytes().to_vec();
bytes.extend(serialize(&tx_out));
bytes.extend(serialize(&outpoint));

// Deserializes successfully despite key + G*1 being unable to spend tx_out
let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// received.offset() == 1, but the wallet key has no relation to tx_out.script_pubkey:
// any downstream spend produces an invalid signature for this input, and if the
// wallet funds a transaction with it, the contributed value is burned.
```

Uncertainty noted: I could not fully confirm whether `send.rs` re-derives/validates the script from `offset` before signing; if it does, the impact reduces from unspendable-funds-accepted to a failed spend attempt. The core invariant gap — `read` bypassing the script/offset linkage enforced by `scan_transaction` — is confirmed in the code above.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
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
  }
```
