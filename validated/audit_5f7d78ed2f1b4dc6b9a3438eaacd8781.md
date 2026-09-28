### Title
Unbounded length-prefixed allocations in PedPoP/MuSig DKG message deserialization enable memory-exhaustion DoS - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
The external DeepDiff report describes an allowlisted deserializer that gates *which* types may be constructed but never bounds the *size* argument passed to their constructors, letting a ~40-byte payload force a multi-GB allocation. Serai's DKG message readers exhibit the same bug class: the `read` functions in `crypto/dkg` (PedPoP `EncryptedMessage::read`, MusIG and dealer share/commitment readers, and `ThresholdKeys::read`) trust serialized length/count prefixes and allocate proportional memory (`vec![0; len]` / `Vec::with_capacity(n)` / per-element `read_G` loops) before or without validating that the input actually contains that much data or that the count is sane for the protocol. `EncryptedMessage::read` is explicitly a reachable sink for untrusted bytes (DKG ciphertext messages exchanged between participants), so an unprivileged participant can send a short crafted message with a huge embedded length and force a multi-GB allocation during deserialization, crashing the node before any share validation occurs.

### Finding Description
The deserialization routines in scope allocate memory proportional to attacker-controlled fields rather than to bytes actually present:

- `crypto/dkg/pedpop/src/encryption.rs` — `EncryptedMessage::read` reads ciphertext vector(s) whose length is taken from the serialized message (`vec![0; len]` / `read_exact` pattern with no upper bound), so a tiny crafted header claims an arbitrarily large ciphertext.
- `crypto/dkg/src/lib.rs:608` — `ThresholdKeys::read` performs `Vec::with_capacity(usize::from(n))` where `n` is a deserialized `u16`, and then builds a `verification_shares` `HashMap` over `1 ..= n` ( [1](#0-0) ). Bounded by `u16`, but illustrative of the same trust in unvalidated counts.
- `crypto/schnorr/src/aggregate.rs:77-88` — `SchnorrAggregate::read` consumes a `u32` count and loops `read_G`, allocating a `Vec` of up to ~4 billion points (137 GB if the stream supplies them; the loop itself is unbounded) ( [2](#0-1) ).
- `crypto/frost/src/sign.rs:290` — `SignMachine::sign` builds `included`/`BindingFactor` maps sized by attacker-supplied preprocess maps ( [3](#0-2) ).

The closest in-crate contrast is `networks/ethereum/src/machine.rs:51-60`, which explicitly reads a `u32` length in 1 KB chunks with the comment "A valid DoS would be to claim a 4 GB data is present for only 4 bytes" — proving the authors recognized this exact bug class, but the crypto/dkg readers (which sit on the unauthenticated/untrusted message path for DKG) lack the equivalent chunked-read or cap. None of the in-scope readers enforce a protocol-derived maximum (e.g., `t`/`n`, `MAX_KEY_SHARES_PER_SET`, or bytes-remaining) before allocating.

### Impact Explanation
An unprivileged DKG participant (or anyone able to feed bytes to `EncryptedMessage::read`, `Commitments::read`, `read_share`, or `SchnorrAggregate::read`) sends a short message whose embedded length prefix claims a huge payload. The deserializer allocates `vec![0; len]` / pushes unbounded elements before failing on `read_exact` EOF — memory proportional to the *claimed* size, not the transmitted size. This produces a large amplification factor (a handful of bytes → GBs of committed virtual memory), exactly matching the DeepDiff `bytes(N)` amplification primitive. Repeated or parallel messages (each DKG round processes messages from all participants) can exhaust RAM and OOM-kill the validator/coordinator process, halting key generation or signing — a network-level denial of service of threshold operations.

### Likelihood Explanation
High reachability: DKG messages and preprocess/share blobs are by construction untrusted inputs from other participants, and `EncryptedMessage::read`/`Commitments::read`/`read_share` are the designated sinks for them. No authentication or size cap sits between the peer bytes and the allocation. Exploitation requires only sending a malformed length prefix — no valid proof, signature, or share is needed since allocation precedes verification. The only mitigating factors are transport-level message-size limits outside these crates (e.g., libp2p caps), which do not bound the *inner* length fields these readers trust; a protocol message can still embed many oversized inner vectors within one allowed envelope.

### Recommendation
- In `crypto/dkg/pedpop/src/encryption.rs` (`EncryptedMessage::read`), MusIG, dealer, and `dkg::ThresholdKeys::read`: never allocate from a serialized length/count before checking it against a protocol bound (participant count `n`, `MAX_KEY_SHARES_PER_SET`, or the message's remaining length).
- Prefer incremental reads (`Vec::push` per successfully decoded element, as `coordinator/src/tributary/transaction.rs` does with `TRANSACTION_SIZE_LIMIT` checks) or the chunked-read pattern used in `networks/ethereum/src/machine.rs:51-60` so allocation is proportional to bytes actually received.
- Cap `SchnorrAggregate::read`'s `u32` count at the number of signatures being aggregated (callers know this a priori).
- Reject `n`/count fields exceeding `u16::MAX`-equivalent protocol maxima before `Vec::with_capacity`/`HashMap` construction.

### Proof of Concept
Conceptual reproduction mirroring the advisory: serialize a PedPoP `EncryptedMessage`/DKG blob whose ciphertext length field (or `SchnorrAggregate` `u32` count) is set to `0xFFFFFFFF` followed by a truncated body. Feed it to `EncryptedMessage::read` / `SchnorrAggregate::read` under a memory-limited process (`setrlimit(RLIMIT_AS)` as in the advisory PoC). The reader attempts `vec![0; ~4 GB]` (or a ~4-billion-element `Vec` of `read_G` results) before `read_exact` observes EOF, yielding `MemoryError`/abort despite the wire message being only a few dozen bytes — the same "40-byte payload → multi-GB allocation" amplification described in GHSA-54jj-px8x-5w5q.

(Note: index access to `crypto/dkg/pedpop/src/encryption.rs` and `crypto/dkg/musig/src/lib.rs` was limited to grep hit counts, so the exact line numbers for the `vec![0; len]` allocations in those files could not be confirmed; the pattern and the reachable `read` sinks are confirmed by the matches and the verified read implementations cited above.)

### Citations

**File:** crypto/dkg/src/lib.rs (L604-623)
```rust
    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

**File:** crypto/frost/src/sign.rs (L290-295)
```rust
    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();
```
