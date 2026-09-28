The analog maps onto `ReceivedOutput::read` in the bitcoin wallet: an attacker-controlled identifier (the HDKD `offset` scalar plus the `script_pubkey` inside the `TxOut`) is deserialized and then trusted as the binding between a spendable output and the secret key used to spend it, with no validation that `script_pubkey == p2tr_script_buf(key + G*offset)` — the exact same shape as `fileID` being trusted to locate a resource without verifying it stays within the intended domain.

### Title
Untrusted `offset`/`script_pubkey` pair in `ReceivedOutput::read` accepted without binding, yielding outputs reported as spendable that are not (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a scalar `offset`, a `TxOut`, and an `OutPoint` from untrusted bytes and returns them as a spendable output, without checking that the `TxOut`'s `script_pubkey` is actually the P2TR script derived from the declared `offset` (`p2tr_script_buf(key + G * offset)`). Downstream signing code (`SignableTransaction`/`multisig`) trusts `output.offset()` to derive the tweaked threshold key for the input, so a mismatched pair makes the "received" output unspendable or makes the signer attempt to spend a script it does not control.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `ReceivedOutput` couples two attacker-controlled fields: [1](#0-0) 

`read` parses `offset` via `Secp256k1::read_F`, then consensus-decodes `TxOut` (containing `script_pubkey`) and `OutPoint` — but never verifies the two are consistent. In the honest flow, `scan_transaction` creates a `ReceivedOutput` whose `offset` is looked up precisely because `output.script_pubkey` matched the registered script: [2](#0-1) 

That lookup is the "sanitized" path — the script→offset binding is established by the `scripts` HashMap keyed on `p2tr_script_buf(key + G*offset)` in `register_offset`: [3](#0-2) 

`ReceivedOutput::read` bypasses this binding entirely, analogous to `Manifest.db`'s `fileID` being used in path construction without verifying it resolves inside the backup directory: here the "identifier" (the claimed offset/outpoint) resolves outside the set of outputs the scanner's key actually controls.

### Impact Explanation
A `ReceivedOutput` whose serialized form is supplied by an untrusted party (the rules explicitly permit untrusted bytes fed to `ReceivedOutput::read`) can claim any `offset` for any `script_pubkey`. Consequences:

- **Funds reported received that are not spendable**: an output whose `script_pubkey` is a scanner-registered script but paired with a wrong `offset` causes the wallet to build a transaction input whose signature is produced under key `key + G*offset_actual` while consensus requires the script's real key — the input can never be validly signed, so the reported funds are unspendable.
- **Signing an unintended spend**: an output whose `script_pubkey` is *not* a scanner-derived script (e.g., a script-path-only or attacker-chosen P2TR key) paired with offset `0` is fed to `SignableTransaction`, causing the threshold group to sign a sighash spending an outpoint to an arbitrary script — the signature is produced under the group key for an input the group never actually received.

Either way the deserialization fabricates a trusted `(offset, script, outpoint)` triple that the honest `scan_transaction` path would never have produced.

### Likelihood Explanation
Requires the integrator to feed attacker-controlled bytes to `ReceivedOutput::read` — e.g., outputs relayed from a peer or reconstructed from an untrusted store rather than produced by `scan_transaction`/`scan_block` locally. This is the same trust model as the MVT advisory (analyst parses a crafted artifact). Within the permitted reachability (untrusted bytes into `read`), it is directly triggerable; the offset and script are fully attacker-chosen 32-byte/serialized fields.

### Recommendation
After decoding, verify the binding the scanner would have established: given the wallet's base `key` (or a registry of expected scripts), check `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)` before returning the `ReceivedOutput`. Alternatively, make `read` a method on `Scanner` so deserialized outputs are re-derived through the `scripts` map exactly as `scan_transaction` does, rejecting any `(script_pubkey, offset)` pair not present in the registry.

### Proof of Concept
```rust
// networks/bitcoin crate, std feature
let key = /* even group key */ ;
let mut scanner = Scanner::new(key).unwrap();
let offset = scanner.register_offset(some_scalar).unwrap();

// Honest output: script for key + G*offset
let honest = /* ReceivedOutput from scan_transaction */;

// Crafted bytes: keep the honest script_pubkey but claim offset = 0
let mut buf = Vec::new();
Scalar::ZERO.write ... // serialize offset = 0
buf.extend(serialize(&honest.output().clone()));   // script of key + G*offset
buf.extend(serialize(&OutPoint::new(fake_txid, 0)));
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// forged.offset() == 0 but forged.output().script_pubkey requires key + G*offset
// SignableTransaction will sign under the untweaked group key -> input unspendable,
// yet the wallet treats it as a received, spendable output.
```

The fix mirrors the MVT patch: validate that the identifier (`offset`) resolves inside the expected domain (the scanner's registered scripts) before using it to locate the resource being operated on.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L185-191)
```rust
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
```

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```
