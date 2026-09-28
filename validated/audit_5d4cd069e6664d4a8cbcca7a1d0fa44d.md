### Title
Unbounded allocation when parsing an aggregate Schnorr signature leads to memory exhaustion - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts a 32-bit, attacker-controlled element count and then deserializes that many group elements into a `Vec<C::G>` with no upper bound. An unprivileged peer who can feed bytes into `SchnorrAggregate::read` (or into any `Read` stream wrapping untrusted input) can declare up to ~4.29 billion `Rs` and stream encodings indefinitely, forcing unbounded heap growth and process termination — the same class as CVE-2022-27819 (unbounded parse of an untrusted/large input → CWE-400/CWE-770 denial of service).

### Finding Description
`crypto/schnorr/src/aggregate.rs:77-88` reads a little-endian `u32` length and loops `C::read_G(reader)` that many times, pushing each point into `Rs`:

```rust
let mut len = [0; 4];
reader.read_exact(&mut len)?;
let mut Rs = vec![];
for _ in 0 .. u32::from_le_bytes(len) {
  Rs.push(C::read_G(reader)?);
}
```

There is no sanity cap on the declared count (contrast `RrCodec::read_request` in `coordinator/src/p2p.rs:261-266`, which rejects lengths above `MAX_LIBP2P_REQRES_MESSAGE_SIZE`, and `Transaction::read` in `coordinator/src/tributary/transaction.rs:286`, which enforces `TRANSACTION_SIZE_LIMIT`). Each `C::G` element (e.g., a dalek `EdwardsPoint`/`ProjectivePoint` from `crypto/dalek-ff-group`, or a `secp256k1`/`ristretto` point) occupies well over 100 bytes on the heap, while each encoding is only 32 bytes, giving a ~4–10x memory amplification per input byte on top of the 4-billion-element ceiling. The function imposes no requirement that the count match any expected signature set; `SchnorrAggregate::verify` only checks `self.Rs.len() != keys_and_challenges.len()` *after* the oversized `Vec` has already been built.

### Impact Explanation
Any node/service that deserializes an aggregate signature from an untrusted stream can be crashed by memory exhaustion. A malicious peer declares `len = u32::MAX` and continuously streams point encodings; the process allocates until the OOM killer terminates it. With `C::G` ≈ 144+ bytes, ~32 bytes of bandwidth yields ~144 bytes of resident memory, so exhausting 8 GB requires under ~2 GB of transferred data — and the `Vec` continues growing to its theoretical ~600 GB ceiling if input persists. This is a remote denial of service of an availability-critical component (threshold signing/verification path) triggered purely by public input bytes, matching the SWHKD advisory's impact profile (Medium, A:H per CVSS).

### Likelihood Explanation
`SchnorrAggregate::read` is a public deserialization API; any protocol path that reads a `SchnorrAggregate` from a peer-controlled byte stream (p2p messages, RPC payloads, signed batches) exposes it. The attacker needs only to supply a 4-byte length followed by valid point encodings — no keys, no threshold position, no collusion. No authentication or state is required before parsing, since deserialization precedes verification. Exploitation cost is low (craft length prefix + stream valid 32-byte encodings; any byte string decodes as *some* curve point for Ristretto, and invalid encodings can simply be repeated valid ones).

### Recommendation
Bound the declared `Rs` count before deserializing: either require callers to pass an expected maximum (e.g., the validator-set size or number of aggregated signatures) and reject `len > expected`, or enforce a hard constant cap. Alternatively, chunk the reads as `Call::read` does in `networks/ethereum/src/machine.rs:51-60`, and pre-check `len` against the remaining stream size where knowable. Document that `read` must only be used on size-bounded streams.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::{Ciphersuite, Ristretto};
use schnorr::SchnorrAggregate;
use group::Group;

// Build a malicious payload: u32::MAX declared Rs, followed by
// an endless/very large stream of valid point encodings.
let mut payload = u32::MAX.to_le_bytes().to_vec();
let enc = <Ristretto as Ciphersuite>::G::generator().to_bytes();
// Repeat a valid encoding; each iteration allocates a new G element
for _ in 0 .. 10_000_000 {
    payload.extend(enc.as_ref());
}

// Each push allocates a full ProjectivePoint (~144+ bytes) per 32 input bytes.
// With a streaming reader, the Vec grows until OOM — up to ~4.29B elements.
let _ = SchnorrAggregate::<Ristretto>::read(&mut Cursor::new(&payload));
```
A stream reader backed by a socket supplying encodings indefinitely reproduces the SWHKD-style "infinite file" condition: the loop has no termination other than the declared `u32` count or EOF.