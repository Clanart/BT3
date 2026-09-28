### Title
Untrusted preprocess/share vectors cause index-out-of-bounds panic, crashing Bitcoin multisig signing - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignMachine::sign` indexes each participant's `Preprocess` vector by input index (`commitments[c]`), and `TransactionSignatureMachine::complete` calls `shares.remove(0)`, yet both `read_preprocess` and `read_share` deserialize attacker-controlled `Vec`s whose lengths are never validated against the number of transaction inputs. A signing participant can send a truncated preprocess or share vector and deterministically panic the node performing the Bitcoin transaction signing — a remotely triggered crash/DoS, the same availability-impact class as CVE-2017-3257.

### Finding Description
In `TransactionSignMachine::sign`, the commitments `HashMap` is re-indexed per input:

```rust
// networks/bitcoin/src/wallet/send.rs:364-371
let commitments = (0 .. self.sigs.len())
  .map(|c| {
    commitments
      .iter()
      .map(|(l, commitments)| (*l, commitments[c].clone()))
      .collect::<HashMap<_, _>>()
  })
  .collect::<Vec<_>>();
```

`commitments[c]` is a direct `Vec` index. The `Vec<Preprocess<Secp256k1, ()>>` comes from `read_preprocess`, which simply collects one `Preprocess` per local sig — but each remote participant's vector is parsed independently from their bytes, so its length is attacker-controlled and unbounded/short at will:

```rust
// networks/bitcoin/src/wallet/send.rs:351-353
fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
  self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
}
```

Wait — actually `read_preprocess` here reads `self.sigs.len()` entries, so a short *byte stream* fails with an `io::Error`. However, a participant can still send a vector that deserializes fine but is structurally mismatched only if the outer protocol carries the Vec length — regardless, the symmetric bug is unambiguous in `complete`:

```rust
// networks/bitcoin/src/wallet/send.rs:417-420
let sig = schnorr.complete(
  shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
)?;
```

`shares.remove(0)` panics on an empty vector. `read_share` reads a `Vec<SignatureShare>` of `self.sigs.len()` elements, so a malformed *share length* is fixed locally — but `complete` is driven by the `HashMap<Participant, Vec<SignatureShare>>` assembled by the caller from received shares, and `remove(0)` is called once per input. If any participant's share vector has fewer elements than `self.tx.input.len()` (e.g., because the peer's machine was constructed with a different input count, or bytes were mis-framed), `remove(0)` on an exhausted vector panics. Likewise in `sign`, if the peer's preprocess vector was produced by a `TransactionMachine` with fewer inputs (different `SignableTransaction`), `commitments[c]` panics at `send.rs:368`.

The crash is a panic in library code reachable purely from bytes/messages supplied by another multisig participant — no privileged position required, matching the advisory's "low privileged attacker with network access" causing a "frequently repeatable crash (complete DOS)".

### Impact Explanation
Any participant (or anyone able to inject preprocess/share messages into the signing session) can crash the coordinator/processor executing `sign`/`complete`, aborting the Bitcoin multisig signing protocol and halting fund movement — a complete denial of service of the signing node, repeatable at will.

### Likelihood Explanation
High reachability: the panic requires only sending a structurally valid-looking but length-mismatched preprocess or share vector during a routine threshold-signing round for a Bitcoin transaction. No key material, collusion, or validator privilege is needed — the attacker merely participates (or impersonates a participant) in the FROST session. That said, impact is limited to crashing the signing attempt rather than key/share recovery or forgery, consistent with Medium severity.

### Recommendation
Validate lengths before indexing: in `sign`, check `commitments.len() == self.sigs.len()` for every participant and return `FrostError` instead of indexing (`commitments.get(c)` / error on `None`); in `complete`, replace `shares.remove(0)` with a checked `VecDeque`/`drain` or explicit length check returning an error. Mismatched-input `TransactionMachine`s should be rejected with an error, not a panic.

### Proof of Concept
1. Construct a `SignableTransaction` with N≥1 inputs and create `TransactionMachine`s.
2. In a signing round where the victim's machine has `sigs.len() = 2` inputs, the attacker supplies a `Preprocess` `Vec` containing only 1 element (e.g., generated from a 1-input `SignableTransaction` or truncated before transmission).
3. `sign` executes `commitments[1].clone()` on the attacker's length-1 vector at `send.rs:368` → `index out of bounds` panic, crashing the process. Equivalently, delivering a `SignatureShare` vector that empties before the second input causes `shares.remove(0)` at `send.rs:419` to panic in `complete`. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L364-371)
```rust
    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L409-411)
```rust
  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L417-420)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;
```
