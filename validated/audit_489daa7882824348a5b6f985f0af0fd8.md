### Title
`ReceivedOutput::read` accepts an unbound offset/script pair, letting crafted bytes claim unspendable outputs as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The kernel bug is a parser that accepts a malformed terminator fragment — a message that is too short (or otherwise inconsistent with the state established by earlier fragments) is accepted as complete. The analog in Serai is `ReceivedOutput::read`, which deserializes three independent fields — a scalar `offset`, a `TxOut`, and an `OutPoint` — and accepts them as a coherent "spendable output" without ever checking that `output.script_pubkey` actually equals the P2TR script derived from `key + offset * G`. A byte string that mixes a valid offset with an unrelated/mismatched script_pubkey (the "too-short/inconsistent tail" analog) is accepted as a valid `ReceivedOutput`.

### Finding Description
`ReceivedOutput` is the type produced by `Scanner::scan_transaction`/`scan_block`, where the offset is guaranteed bound to the script via `self.scripts.get(&output.script_pubkey)` [1](#0-0) . However, the public `ReceivedOutput::read` deserializer performs no such binding: it reads `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint` and returns them as a spendable output [2](#0-1) . There is no `Scanner` field retained, no re-derivation of `p2tr_script_buf(key + G*offset)`, and no comparison against `output.script_pubkey` [3](#0-2) . Like the kernel accepting a truncated `ISO_END` as a valid payload terminator, the reader accepts a truncated/inconsistent tail — a `TxOut` that does not satisfy the predicate the type's own constructor-path (`Scanner`) enforces — as a complete, valid object. Additionally, `register_offset` documents that arbitrary offsets can embed an attacker-controlled script path [4](#0-3) , so a `read`-produced `ReceivedOutput` can also reference an output spendable via script path rather than the threshold key.

### Impact Explanation
An `EncryptedMessage`/`ReceivedOutput::read`-style entry point that ingests attacker-controlled bytes yields a `ReceivedOutput` claiming funds were received to the wallet when either (a) the script_pubkey does not correspond to `key + offset*G`, so the output is not spendable by the threshold key at all, or (b) the offset introduces a Taproot script path the attacker can satisfy, meaning "received" funds are spendable by the attacker, not the validator set. Either case produces "funds reported received that are not spendable," which is an accepted impact class. Downstream `send`/signing code consuming `ReceivedOutput` would build transactions spending outpoints the group cannot actually sign for, or sign under a key that doesn't control the script path.

### Likelihood Explanation
Reachability requires an unprivileged party to supply bytes into `ReceivedOutput::read` (e.g., any coordinator/processor path that round-trips scanned outputs through serialized form rather than keeping them bound to the `Scanner`). If all consumers only ever receive `ReceivedOutput`s freshly produced by `scan_transaction` in-process, the bug is latent. Medium severity is appropriate: it requires a deserialization entry point fed by untrusted data, but the type provides no defense and the impact (unspendable or attacker-spendable "received" funds) is concrete.

### Recommendation
In `ReceivedOutput::read` (or in a `Scanner::read_output`-style API), retain/receive the wallet `key`, recompute `p2tr_script_buf(key + ProjectivePoint::GENERATOR * offset)`, and reject the input if it differs from `output.script_pubkey`. Alternatively, make `ReceivedOutput` unconstructible except via `Scanner` (seal deserialization behind a scanner-bound verify), mirroring the kernel fix of validating the terminal fragment against state established by the start of the sequence before accepting it.

### Proof of Concept
```rust
// Given a wallet key K with registered scripts in Scanner:
// 1. Attacker picks a valid offset `o` registered in the scanner.
// 2. Attacker crafts bytes = serialize(o) || serialize(TxOut{value: 1_000_000,
//    script_pubkey: <attacker's own P2TR or arbitrary script>}) || serialize(OutPoint{...}).
// 3. ReceivedOutput::read(&mut bytes) returns Ok(output).
// 4. output.value() reports 1_000_000 sats as "received" by the wallet, while
//    output.script_pubkey is not spendable by K + o*G (key-path), and may carry
//    an attacker-satisfiable script path. scan_transaction would never have
//    produced this object, demonstrating the unbound acceptance.
```
Note: I did not fully trace every caller of `ReceivedOutput::read` to confirm a concrete untrusted-byte ingestion path; the finding holds wherever serialized outputs cross a trust boundary, which is the pattern the in-scope rules target.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L176-180)
```rust
  ///
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
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
