### Title
`ReceivedOutput` deserializes an arbitrary scalar offset without binding it to the output's `script_pubkey`, allowing unspendable "received" outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is an operation permitted while a required precondition (staking having started) is unchecked, breaking an internal invariant. The closest reachable analog in scope is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`: it accepts any `(offset, TxOut, OutPoint)` triple from untrusted bytes with no verification that `offset` actually derives the Taproot `script_pubkey` contained in `output`. `Scanner::scan_transaction` is the only code path that establishes this binding (it inserts `offset` only when `self.scripts` maps the output's `script_pubkey`, where each entry was built as `p2tr_script_buf(key + G*offset)`), but `read()` bypasses it entirely. A deserialized `ReceivedOutput` can therefore claim an offset that does not yield the key controlling the output. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ReceivedOutput::read` performs three independent parses — `Secp256k1::read_F` for the offset, `TxOut::consensus_decode`, and `OutPoint::consensus_decode` — and returns the struct without any consistency check. The struct's invariant, established only inside `Scanner` (`self.scripts.get(&output.script_pubkey)` returning the registered offset), is that `output.script_pubkey == p2tr_script_buf(scanner_key + G*offset)`. Downstream spending code (`send.rs`, in the same directory) uses `offset()` to tweak the threshold keys (via `ThresholdKeys::offset` / the taproot tweak flow in `tweak_keys`) and produces a Schnorr signature under that tweaked key. If the offset does not derive the output's script, the signature verifies under the wrong key: the spend is consensus-invalid and the "received" funds are not spendable by the set. Nothing re-derives the script from `(key, offset)` at read or spend time to enforce the invariant — the check exists only implicitly inside `Scanner`, exactly the "missing explicit status/precondition check" pattern of the report. [4](#0-3) [5](#0-4) 

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` bytes (a listed reachability sink) can cause an output to be treated as received/spendable under a threshold-key offset that does not control it. The node then attempts to spend a UTXO under a mismatched tweaked key: the resulting transaction is invalid and the real funds are never claimed (funds reported received that are not spendable). This is a Medium-severity integrity/availability impact — no key leakage or forgery, but a concrete violation of the spendability invariant from public input bytes.

### Likelihood Explanation
Requires attacker-controlled serialized `ReceivedOutput` data to reach the spending pipeline rather than `Scanner` output. `read()` itself performs zero contextual validation, so wherever such bytes are accepted (cross-component messages, storage of unverified data), the flaw triggers deterministically. It does not require a malicious validator, collusion, or leaked key — only untrusted bytes at the documented sink.

### Recommendation
Bind the offset to the output at the trust boundary: either (a) verify inside `ReceivedOutput::read`/a `verify(&self, scanner_key)` helper that `p2tr_script_buf(scanner_key + G*offset) == Some(output.script_pubkey)` (with the even-Y/None handling mirroring `register_offset`), or (b) restrict `ReceivedOutput` construction to `Scanner` results and reject externally supplied offsets, mirroring the report's fix of explicitly checking contract state before executing.

### Proof of Concept
1. Take any real on-chain P2TR output `O` not belonging to the Serai key (e.g., a stranger's Taproot UTXO), plus an arbitrary `offset` scalar.
2. Serialize `offset || consensus_encode(O) || consensus_encode(O.outpoint)` and feed it to `ReceivedOutput::read` — it succeeds.
3. Hand the result to the send path: keys are tweaked by `offset` and signed; the signature key does not equal `O.script_pubkey`'s key, so Bitcoin consensus rejects the spend. The output was accepted as received yet is unspendable.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L45-75)
```rust
#[cfg(feature = "std")]
pub fn tweak_keys(keys: ThresholdKeys<Secp256k1>) -> ThresholdKeys<Secp256k1> {
  // Adds the unspendable script path per
  // https://github.com/bitcoin/bips/blob/master/bip-0341.mediawiki#cite_note-23
  let keys = {
    use k256::elliptic_curve::{
      bigint::{Encoding, U256},
      ops::Reduce,
      group::GroupEncoding,
    };
    let tweak_hash = TapTweakHash::hash(&keys.group_key().to_bytes().as_slice()[1 ..]);
    /*
      https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki#cite_ref-13-0 states how the
      bias is negligible. This reduction shouldn't ever occur, yet if it did, the script path
      would be unusable due to a check the script path hash is less than the order. That doesn't
      impact us as we don't want the script path to be usable.
    */
    keys.offset(<Secp256k1 as Ciphersuite>::F::reduce(U256::from_be_bytes(
      *tweak_hash.to_raw_hash().as_ref(),
    )))
  };

  let needs_negation = needs_negation(&keys.group_key());
  keys
    .scale(<_ as subtle::ConditionallySelectable>::conditional_select(
      &Scalar::ONE,
      &-Scalar::ONE,
      needs_negation,
    ))
    .expect("scaling keys by 1 or -1 yet interpreted as 0?")
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L88-113)
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
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
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
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
```
