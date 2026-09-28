### Title
Attacker-controlled length prefix in `SchnorrAggregate::read` causes unbounded deserialization loop - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` reads a 4-byte little-endian length `len` directly from the input stream and then iterates `for _ in 0 .. u32::from_le_bytes(len)`, calling `C::read_G(reader)` on every iteration, with no cap on the count and no comparison against the amount of data actually present or against `keys_and_challenges.len()` the signature will later be checked with. An unprivileged party supplying serialized `SchnorrAggregate` bytes can set `len` to `u32::MAX` (~4.3 billion), forcing up to ~4.3 billion group-element decompressions — each involving a `read_exact`, byte-slice bounds work, and point-from-bytes decompression — before any verification ever occurs. This is the same bug class as the reference report: a loop bound derived from an external value that is never clamped to the legitimate bound (`rewardPeriods` / actual element count), producing resource exhaustion.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`:

```rust
pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
  let mut len = [0; 4];
  reader.read_exact(&mut len)?;

  let mut Rs = vec![];
  for _ in 0 .. u32::from_le_bytes(len) {
    Rs.push(C::read_G(reader)?);
  }

  Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
}
```

(lines 77–88). The loop trip count is entirely attacker-controlled via the first four bytes. There is no `if len > MAX` check and no early termination tied to the caller's expected count. While `read_G` will eventually fail on a truncated finite buffer, the reader is generic over `io::Read` — a caller feeding a streaming reader (network socket, `io::Cursor` over a large allocation, or a reader that synthesizes bytes) causes the loop to run as long as bytes are supplied, growing `Rs` unboundedly (each `C::G` pushed, so memory grows linearly too). Additionally, `verify` at line 128 already knows the legitimate bound — `keys_and_challenges.len()` — but `read` has no way to enforce it, mirroring the report's "loop should not go beyond `rewardPeriods`".

The analogous shape holds throughout the deserialization surface listed in scope (`Commitments::read`, `EncryptedMessage::read`, `ThresholdKeys::read`, `DLEqProof::read`), but `aggregate.rs` is the clearest case because the length is a raw u32 with no protocol-level bound — the aggregator itself only ever aggregates signatures the participant actually received, so honest aggregates are small.

### Impact Explanation
Any component that deserializes an aggregate Schnorr signature from untrusted bytes (e.g., bytes relayed between participants/coordinators during half-aggregation verification) can be stalled or OOM-killed by a single small prefix `[0xFF, 0xFF, 0xFF, 0xFF]` followed by a stream of syntactically valid points. Because `read` performs full point decompression per iteration — not just a cheap counter — the cost per claimed element is high. This is a remote denial of service of a node/coordinator reachable purely through attacker-supplied message bytes, with no key material, collusion, or privileged position required. Severity: Medium (DoS only; no forgery or secret leakage, and the signature can never verify, so no consensus corruption).

### Likelihood Explanation
Reachable whenever serialized `SchnorrAggregate` bytes cross a trust boundary — e.g., an aggregator/coordinator collecting aggregate signatures from peers. The attacker controls exactly four bytes; no valid signature, valid point encodings beyond sustaining the loop, or protocol cooperation is needed. The only mitigating factor is that a fixed-size in-memory buffer causes early `read_exact` failure, so exploitability depends on the caller using a streaming reader or a buffer padded with valid encodings; the code makes no defensive check either way.

### Recommendation
Cap the loop by the legitimate bound before deserializing: pass the expected maximum statement count (or enforce a hard protocol constant) into `read`, and reject `len` values exceeding it before looping — e.g., `if u32::from_le_bytes(len) > expected { return Err(...) }`. Equivalently, bound the read by the buffer's actual remaining length (each `C::G` has a fixed encoded size, so `remaining / C::G::Repr::default().as_ref().len()` gives the true maximum element count). The loop must not iterate beyond the number of elements the protocol can legitimately contain, just as the report's loop must not iterate beyond `rewardPeriods`.

### Proof of Concept
```rust
use std::io::Cursor;
use schnorr::SchnorrAggregate;
use ciphersuite::Ed25519; // any Ciphersuite

// Craft a malicious serialized aggregate: len = u32::MAX
let mut bytes = vec![0xFF, 0xFF, 0xFF, 0xFF];
// Append as many valid Ed25519 point encodings as desired to sustain the loop;
// with a streaming Read, each supplied point = one full decompression.
bytes.extend_from_slice(&[0x01; 32].repeat(1_000_000));

let mut reader = Cursor::new(&bytes);
// Loops up to ~4.3 billion times (or until the stream ends), pushing each
// decompressed point into an ever-growing Vec — CPU + memory exhaustion.
let _ = SchnorrAggregate::<Ed25519>::read(&mut reader);
```

With a fixed buffer the loop still performs up to `buffer_len / 32` decompressions and allocations for a `len` that no honest aggregate could ever produce; with a streaming reader the work is effectively unbounded.

Relevant code: [1](#0-0)  and the verification bound that read ignores: [2](#0-1)

### Citations

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

**File:** crypto/schnorr/src/aggregate.rs (L127-130)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }
```
