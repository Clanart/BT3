### Title
`ReceivedOutput::read` accepts an unvalidated key-derivation `offset` from untrusted bytes, letting a forged output be attributed to (and "spent" under) an arbitrary key tweak - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
This is analogous to CVE-2026-67307: Wazuh trusted `cluster_name`/`cluster_node` fields inside a signed message instead of deriving them from the authenticated identity, enabling attribution spoofing. In `bitcoin-serai`, `ReceivedOutput::read` deserializes the `offset` scalar — the field that determines *which* tweaked key an output is attributed to — straight from attacker-controlled bytes, with no check that `p2tr_script_buf(group_key + G·offset) == output.script_pubkey`. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput` is the record that ties an on-chain output to a key-derivation offset. When produced honestly, the offset comes from `Scanner::scan_transaction`, which only sets `offset` after matching `output.script_pubkey` against the registered `script → offset` map, so the binding `key + G·offset → script_pubkey` holds by construction.

`ReceivedOutput::read`, however, reconstructs that same structure from raw bytes: it reads a `Scalar` via `Secp256k1::read_F`, then a `TxOut` and `OutPoint` via consensus decoding, and returns the tuple verbatim. There is no re-derivation of the script, no comparison against `output.script_pubkey`, and no check that the offset is one the scanner ever registered. The claimed attribution field is accepted exactly like Wazuh's `cluster_name`.

Downstream, `send.rs` builds one `AlgorithmSignMachine<Secp256k1, Schnorr>` per input and signs each `taproot_key_spend_signature_hash` under a key re-derived per input — the offset on the `ReceivedOutput` is what selects that per-input key tweak (the "signing key identity"). An attacker supplying a forged `ReceivedOutput` can therefore:

- attribute an unrelated `TxOut`/`OutPoint` to any offset they choose (spoofed attribution — the direct analog of forging `wazuh.cluster.name` / `_id` prefix);
- force the wallet to sign a spend of an output whose `script_pubkey` does not match the tweaked key it will sign with.

Additionally, `register_offset` is explicitly surjective — different offsets can collide onto the same `script_pubkey` — so even honestly serialized records can carry an offset that a forger can replay against a *different* `TxOut` while passing any future consistency check that only validates the offset against the registered set. [3](#0-2) [4](#0-3) 

### Impact Explanation
The sighash commits to `Prevouts::All`, so the signature binds to whatever `TxOut`s the attacker wrote into the forged records. Two concrete consequences:

1. **Funds reported received that are not spendable**: a record pairing a real deposit outpoint with a wrong offset yields a signature under a key that does not correspond to `output.script_pubkey`; the broadcast transaction fails script verification, and the wallet treats a spendable UTXO as consumed/planned while producing no valid spend.
2. **Attribution poisoning**: a record pairing a *foreign* `script_pubkey` with a registered offset lets an attacker inject outputs into the wallet's receive/spend pipeline, inflating reported balances with UTXOs the group key cannot spend — the same "poisoning another party's records via a spoofed identifier" shape as the Wazuh bug.

Note: I verified the deserialization and scanner paths directly; the exact line in `send.rs` where `output.offset()` re-keys the per-input sign machine is in code beyond the fetched snippet (`TransactionSignMachine` is constructed from the `SignableTransaction`'s inputs at networks/bitcoin/src/wallet/send.rs:321-330), so that call-site detail is inferred rather than line-cited.

### Likelihood Explanation
Reachability requires `ReceivedOutput::read` to consume bytes an unprivileged party can influence — e.g., outputs relayed from an indexer/scanner peer or restored from shared/writable storage. That is precisely the untrusted-bytes channel the audit scope allows. No key material, collusion, or protocol violation by a validator is needed; the attacker only crafts bytes. Severity is Medium: no secret leakage or valid forgery results (the sighash still binds the tx), but funds can be reported received yet be unspendable, and record attribution can be spoofed, matching the reference CVSS 6.0 integrity profile.

### Recommendation
- In `ReceivedOutput::read` (or a dedicated `verify`/`rekey` step before use in `SignableTransaction`), re-derive `p2tr_script_buf(group_key + G·offset)` and reject the record unless it equals `output.script_pubkey`, mirroring the fix that derives `cluster_name`/`cluster_node` server-side.
- Alternatively, re-run `Scanner`-side lookup on the decoded `script_pubkey` and overwrite `offset` with the registered value, ignoring the wire field.
- Document that `offset` must never be trusted from serialized form, parallel to the existing warning that registered offsets "must be securely generated" (networks/bitcoin/src/wallet/mod.rs:177-179).

### Proof of Concept
1. Register a legitimate offset `o` so `script_A = p2tr(key + G·o)` is in `Scanner.scripts`.
2. Craft bytes: `offset = o' (arbitrary)`, `output = TxOut { value: 1 BTC, script_pubkey: script_B }` where `script_B` is an attacker-controlled script (not in the scanner map), `outpoint = a real confirmed outpoint`.
3. Feed the bytes to `ReceivedOutput::read` — parsing succeeds; the object claims offset `o'` for an output the group key cannot spend.
4. Pass the record into `SignableTransaction`/`TransactionSignMachine`: the signer re-keys with `o'` and produces a Schnorr signature over a valid sighash, but the signature verifies against `x_only(key + G·o' + tweak)` ≠ `script_B`'s key — the input is unspendable, while the wallet has recorded foreign funds as received under its registered offset.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L168-196)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-395)
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
```
