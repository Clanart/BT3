### Title
Malformed preprocess vector length crashes the FROST signing machine via out-of-bounds indexing - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignMachine::sign` indexes each co-signer's preprocess `Vec` by input index without checking its length. A participating signer can supply a `Vec<Preprocess>` (deserialized via `read_preprocess`) shorter than the number of transaction inputs, causing an out-of-bounds index panic and crashing the signing process — a remotely-triggerable denial of service analogous to the crash-only DoS class of CVE-2020-2679.

### Finding Description
`TransactionSignMachine` wraps one `AlgorithmSignMachine` per transaction input (`self.sigs`), and `read_preprocess` deserializes each peer's preprocess as a plain `Vec<Preprocess<Secp256k1, ()>>` by mapping `sig.read_preprocess(reader)` over `self.sigs` — but the *outer* vector's length is whatever the peer encoded, and it is never validated against `self.sigs.len()` [1](#0-0) .

In `sign`, the per-input commitment maps are built by indexing into each signer's preprocess vector:

```rust
let commitments = (0 .. self.sigs.len())
  .map(|c| {
    commitments
      .iter()
      .map(|(l, commitments)| (*l, commitments[c].clone()))
      .collect::<HashMap<_, _>>()
  })
  .collect::<Vec<_>>();
``` [2](#0-1) 

`commitments[c]` panics with `index out of bounds` for any signer whose serialized preprocess vector has fewer than `self.sigs.len()` elements. The vector length is fully attacker-controlled bytes fed through `read_preprocess`; the panic occurs inside `SignMachine::sign` before any signature share is produced.

### Impact Explanation
An unprivileged participant in a Bitcoin multisig signing session (i.e., anyone whose preprocess bytes are accepted by `read_preprocess`) can abort and crash the host process — or at minimum the signing task, via panic — on demand and repeatedly, by sending a truncated preprocess vector. This is a complete availability loss for the signing operation matching the "hang or frequently repeatable crash" impact class. Every input index `c` in `0..sigs.len()` is unconditionally dereferenced on each signer, so a single malformed party reliably triggers the panic on every honest signer that calls `sign` with the malformed map.

### Likelihood Explanation
- Reachability: `TransactionSignMachine::read_preprocess` is the documented public ingestion point for untrusted preprocess bytes [1](#0-0) .
- Preconditions: the attacker only needs to be a recognized signing participant delivering a preprocess message — no privilege, collusion, or key material required.
- Reliability: 100% deterministic panic; the indexing is unconditional for every input.
- Mitigating factor: it is a panic (clean unwind at an FFI/task boundary if caught) rather than memory unsafety, so the blast radius is a crashed signing attempt/task — consistent with Medium severity.

### Recommendation
Validate the length of each signer's preprocess vector in `TransactionSignMachine::sign` before indexing: return `Err(FrostError::...)` if any `commitments[l].len() != self.sigs.len()`. Alternatively, build the maps with `get(c)`/`ok_or` and propagate an error. Consider also checking count equality in `read_preprocess` call sites so malformed preprocesses are rejected at deserialization time.

### Proof of Concept
Conceptual trigger (assuming `Schnorr`'s `Preprocess` serialization admits an empty vec, e.g. a `u32`/`u16` length prefix of `0`):

```rust
// Construct a valid SignableTransaction with 1 input, get TransactionMachine,
// call preprocess() to obtain TransactionSignMachine `tsm`.
let mut commitments: HashMap<Participant, Vec<Preprocess<Secp256k1, ()>>> = HashMap::new();
// Attacker's preprocess is a length-prefixed empty Vec:
let malformed: &[u8] = &[0u8; LEN_PREFIX_SIZE];
commitments.insert(
    Participant::new(2).unwrap(),
    tsm.read_preprocess(&mut &malformed[..]).unwrap(), // Ok(vec![])
);
// Panic: index out of bounds at send.rs:368 (`commitments[0]` on empty vec)
let _ = tsm.sign(commitments, b"");
```

The `msg.is_empty()` guard at `send.rs:360-361` is satisfied with `b""`, so execution reaches the `commitments[c]` indexing and panics deterministically.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L351-353)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }
```

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
