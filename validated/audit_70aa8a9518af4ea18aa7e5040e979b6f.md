### Title
`ReceivedOutput::read` accepts untrusted `(offset, TxOut, OutPoint)` tuples without validating that the output's `script_pubkey` actually commits to `key + offset*G` — funds reported as received that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a scalar `offset`, a `TxOut`, and an `OutPoint` from a byte stream with no consistency check binding them together or to the scanner's key. Elsewhere in the same module, `Scanner::scan_transaction` only produces a `ReceivedOutput` after confirming `self.scripts.get(&output.script_pubkey)` succeeds, i.e. that the output pays to `p2tr(key + offset*G)` — an invariant `ReceivedOutput::read` neither checks nor can rely on. An untrusted byte stream can therefore mint a `ReceivedOutput` whose `offset` does not yield the `TxOut`'s `script_pubkey` (or whose `outpoint` does not even reference a real UTXO), and downstream code will treat it as a spendable, received payment.

### Finding Description
- `Scanner::scan_transaction` establishes the critical invariant `output.script_pubkey == p2tr(key + offset*G)` before constructing a `ReceivedOutput`, and `Scanner::register_offset` additionally guarantees the resulting point has even Y (incrementing the offset until `p2tr_script_buf` returns `Some`). [1](#0-0) 
- `p2tr_script_buf` returns `None` for odd-Y points, so an odd `key + offset*G` is unusable as a Taproot output key at all. [2](#0-1) 
- `ReceivedOutput::read` accepts a `Secp256k1` scalar via `read_F`, a consensus-decoded `TxOut`, and a consensus-decoded `OutPoint`, then returns the tuple as a fully-formed `ReceivedOutput`. It performs zero checks that (a) the offset produces an even point, (b) `key + offset*G` corresponds to `output.script_pubkey`, or (c) `outpoint` refers to a transaction output equal to `output`. [3](#0-2) 
- The module's own documentation acknowledges that arbitrary offsets are dangerous ("Arbitrary offsets may introduce a script path into the output"), which is exactly why `register_offset`/`scan_transaction` are the only trusted producers — `read` bypasses all of those guarantees. [4](#0-3) 

This is a direct analog of the CVE class (insufficient data validation allowing a security-property bypass): the scanner's validation of the critical offset↔script binding is silently dropped for the deserialization path.

### Impact Explanation
A party able to feed bytes to `ReceivedOutput::read` can cause the processor to record a deposit that is unspendable or nonexistent:

1. **Offset/script mismatch**: supply any valid `TxOut` paying to an attacker-controlled P2TR key, plus an offset that does not satisfy `key + offset*G == txout_key`. The output is reported received and accounted at `output.value`, but the FROST signing set cannot produce a valid BIP-340 key-path signature for it — funds are reported received that are not spendable.
2. **Odd-parity offset**: supply an offset for which `key + offset*G` has odd Y. No valid P2TR output key exists for it; any spend attempt is unspendable by construction.
3. **Phantom outpoint**: supply an `OutPoint` that was never created on-chain together with a fabricated `TxOut`. The system credits value that cannot ever be spent.

In a bridge/processor setting, this inflates credited balances backed by outputs that cannot be moved, i.e. an accounting/solvency discrepancy.

### Likelihood Explanation
Requires an attacker to control the byte input to `ReceivedOutput::read` (e.g. received-output data relayed between components rather than derived locally via `Scanner`). Where outputs flow exclusively through `scan_transaction`, this is unreachable; where serialized `ReceivedOutput`s are imported from peers/storage, it is directly reachable with purely public inputs. Severity is bounded by downstream accounting treatment, so Medium.

### Recommendation
Either re-validate in `ReceivedOutput::read` — taking the scanner `key` (or the `ScriptBuf` map) as a parameter and rejecting inputs where `p2tr_script_buf(key + offset*G) != Some(output.script_pubkey)` — or mark the function `unsafe`/document that callers MUST verify the offset↔script binding and outpoint existence before accounting the output as received.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ReceivedOutput::read:
//   offset = 1 (arbitrary scalar)
//   output = TxOut { value: 100_000, script_pubkey: <attacker's own P2TR key> }
//   outpoint = OutPoint::new(<attacker's txid>, 0)
let ro = ReceivedOutput::read(&mut bytes).unwrap(); // succeeds, no checks

// Invariant required by Scanner::scan_transaction does NOT hold:
assert_ne!(
  p2tr_script_buf(scanner_key + ProjectivePoint::GENERATOR * ro.offset()),
  Some(ro.output().script_pubkey.clone()),
);
// ro.value() reports 100_000 sats received, but no key-path signature
// the threshold group produces can spend it.
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L168-180)
```rust
  /// Register an offset to scan for.
  ///
  /// Due to Bitcoin's requirement that points are even, not every offset may be used.
  /// If an offset isn't usable, it will be incremented until it is. If this offset is already
  /// present, None is returned. Else, Some(offset) will be, with the used offset.
  ///
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
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
