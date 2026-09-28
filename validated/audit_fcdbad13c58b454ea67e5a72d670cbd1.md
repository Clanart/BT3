### Title
`ReceivedOutput::read` accepts an offset that is never bound to the output's script_pubkey, allowing spendability mismatches that lock funds — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is: a caller-supplied parameter (`authority_bump`) used to derive the authority/PDA under which collateral is locked is never checked against the stored `borrow_request.authority_bump`, so collateral ends up attributed to an address that the later withdraw/seize path cannot reproduce, permanently locking funds. In Serai's Bitcoin wallet, `Scanner` derives a P2TR script from `key + offset·G` and stores the `script → offset` binding in `self.scripts` (`register_offset`, `scan_transaction`). `ReceivedOutput` carries only `{offset, output, outpoint}` — the group key and the script↔offset binding are dropped. `ReceivedOutput::read` deserializes `offset` via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via consensus decoding with no consistency check that `output.script_pubkey == p2tr(key + offset·G)` for the wallet's key (it cannot check — the key isn't even part of the type). [1](#0-0) 

### Finding Description
`scan_transaction` is the only code that constructs a coherent `ReceivedOutput`: it looks up `self.scripts.get(&output.script_pubkey)` so the returned `offset` is guaranteed to satisfy `output.script_pubkey == p2tr(key + offset·G)`. [2](#0-1)  Serialization via `write`/`serialize` emits just `offset || TxOut || outpoint`. [3](#0-2)  `read` reconstructs the struct field-by-field without recomputing or re-deriving anything — it cannot even detect a corrupted/malicious pairing because `ReceivedOutput` does not store the key the offset is relative to. [4](#0-3)  This is exactly the report's shape: two values that must be equal for later withdrawal to work (here: the offset that unlocks the script vs. the script actually paid) are read from independent, unauthenticated bytes, and the invariant is only ever established implicitly in the scanner, never re-verified on the ingest path.

Downstream, the spend key for a `ReceivedOutput` is `secret_share·scalar + offset` — the wallet signs for `key + offset·G`. If `read` is fed bytes where `offset` does not match the `script_pubkey` actually carried in `output`, the resulting signatures can never satisfy the output's Taproot key-path: the funds are on-chain, reported as received, yet unspendable. The analogous failure to "all previously added collateral becomes locked" is that a single mismatched output in a batch gets scheduled into a transaction plan whose signing then fails or burns fees.

### Impact Explanation
Funds reported received that are not spendable: a `ReceivedOutput` whose serialized `offset` was altered (or recombined with another output's bytes) passes `read` cleanly, is treated as a valid received output, and any transaction built over it produces signatures under `key + offset·G` that the real `script_pubkey` rejects. The output is bricked for the wallet and may wedge a plan containing it — the same locked-collateral consequence as the original report. Medium: requires untrusted/mutable bytes reaching `ReceivedOutput::read`, but that is precisely the untrusted-deserialization surface in scope.

### Likelihood Explanation
`read` is a pure deserialize-and-trust path; there is no redress — no key stored, no re-derivation, no checksum binding offset to script. Any consumer that round-trips scanner results through storage or transport that an attacker (or a corrupted peer/DB entry) can touch inherits the flaw with certainty once touched. The missing check is unconditional, not probabilistic.

### Recommendation
Bind the offset to the output at (de)serialization time: either store the wallet `key` in `ReceivedOutput` and assert `p2tr_script_buf(key + G·offset) == output.script_pubkey` inside `read` (mirroring the report's `authority_bump == borrow_request.authority_bump` check), or transcript/authenticate serialized `ReceivedOutput`s (MAC or store them only in authenticated storage). At minimum, re-scan `output.script_pubkey` against the live `Scanner` after deserialization before treating the output as spendable.

### Proof of Concept
1. `Scanner::new(key)` then `register_offset(o)` returns the used offset `o'` (possibly `o` incremented to an even point). `scan_transaction` emits `ReceivedOutput { offset: o', output: TxOut{script_pubkey: p2tr(key + o'·G), ..}, .. }`. [5](#0-4) 
2. Serialize it with `serialize()`. Flip the offset bytes to `o'' ≠ o'` (or splice the offset field from a different registered offset's output).
3. `ReceivedOutput::read` succeeds — `Secp256k1::read_F` accepts any canonical scalar; nothing compares `o''` against `output.script_pubkey`. [6](#0-5) 
4. The wallet later spends using key `key + o''·G`; the Taproot output actually requires `key + o'·G`. Signing "succeeds" locally, the transaction is unspendable on-chain, and the output (and any plan batching it) is locked — the same consequence as `AddCollateral` with a mismatched `authority_bump` permanently blocking withdrawal.


In bsaldua/serai--011, networks/bitcoin/src/wallet/mod.rs: `ReceivedOutput::read` (lines ~120-134) deserializes `offset`, `output`, and `outpoint` without verifying that `output.script_pubkey` equals `p2tr_script_buf(key + GENERATOR * offset)` — the binding established in `Scanner::scan_transaction`/`register_offset` (lines ~180-213) is lost on serialization. Add a consistency check so a deserialized `ReceivedOutput` cannot pair an offset with a script it does not unlock (e.g., add the group key to `ReceivedOutput`/`read` and re-derive the script via `p2tr_script_buf`, rejecting mismatches with `io::Error`), or document/enforce an authenticated-storage requirement. Add a regression test in networks/bitcoin/tests/wallet.rs that mutates the serialized offset and asserts `read` fails or the output is rejected before spending.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-148)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
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
