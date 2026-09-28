### Title
Unbounded length-prefixed deserialization enables indefinite blocking / memory exhaustion in `SchnorrAggregate::read` - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::<C>::read` trusts an attacker-controlled 4-byte little-endian count and then loops that many times calling `C::read_G(reader)`. An unprivileged party supplying the serialized bytes can set the count to `u32::MAX` (~4.29 billion) with no corresponding data, forcing the callee to either block indefinitely inside `read_exact` waiting for bytes that never arrive, or — for an in-memory reader — spin through up to billions of point-deserialization attempts and grow a `Vec<C::G>` without bound. This is the same vulnerability class as CVE-2023-38505 (DietPi-Dashboard): a single parse slot is held hostage forever by a peer that simply withholds the expected data, denying service to every other caller.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, the deserializer reads a raw length prefix and iterates it verbatim:

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
``` [1](#0-0) 

Three compounding defects:

1. **No bound on `len`.** Unlike sibling deserializers, the count is not validated against any protocol maximum. Compare `Commitments::read`, which is bounded by `params.t()` (a `u16` ≤ 65535), and `ThresholdKeys::read`, bounded by a `u16` `n`. `SchnorrAggregate::read` uses a `u32`, so the attacker chooses up to ~4.29 billion iterations.
2. **Indefinite blocking.** `C::read_G` performs `reader.read_exact` into a fixed-size encoding buffer. When `R` is any streaming/blocking reader (a socket, pipe, or channel-backed `Read`), a prefix of `FF FF FF FF` followed by silence leaves the call parked inside `read_exact` forever — the exact "waits indefinitely for data that never starts" primitive from the DietPi advisory. Whoever invokes `read` (signature-verification pipeline, coordinator parsing of submitted aggregates) loses that thread/slot permanently.
3. **Memory/CPU exhaustion for buffered input.** Each successful `read_G` pushes a `C::G` into `Rs`; there is no `with_capacity` sanity check or early rejection, so a moderately sized input claiming a huge `len` forces allocation growth and up to billions of deserialization attempts before `io::ErrorKind::UnexpectedEof` is ever returned.

`SchnorrAggregate::read` is a public API in an in-scope crate (`crypto/schnorr`), and the rules explicitly list untrusted bytes fed to `read`-family functions as a reachable surface.

### Impact Explanation
Availability loss, mirroring the High-severity advisory: an attacker who can cause a node/verifier to call `SchnorrAggregate::read` on bytes they control can either (a) freeze the parsing thread indefinitely by sending a large length prefix and then nothing — a permanent single-slot stall identical to the withheld-TLS-handshake DoS — or (b) burn CPU and heap by providing a stream that keeps the ~4-billion-iteration loop alive. Any component that deserializes aggregates from peers or submitted transactions can be taken down with a 4-byte message.

### Likelihood Explanation
Likelihood depends on integrators feeding network-supplied bytes to `SchnorrAggregate::read`, which is the designed purpose of a `Read`-based deserializer for a signature type meant to be transmitted and verified. The attack requires no privileges, no valid key material, and only 4 bytes of input (`0xFFFFFFFF` + EOF or a drip-fed stream). Since the library performs no length sanity check and no element-count cap, every caller inherits the exposure by default.

### Recommendation
- Derive `Rs` length from context rather than the wire: have `read` take the expected signer/challenge count (as `Commitments::read` takes `ThresholdParams`, and `NonceCommitments::read` takes `generators`), and reject inputs where the declared count differs.
- If a length prefix must be trusted, cap it at a protocol maximum (e.g., `u16::MAX` or the validator-set size) before looping, and pre-check `reader` remaining length when the reader supports it.
- Integrators should wrap untrusted input in a bounded/`take(n)` reader and enforce read deadlines so `read_exact` cannot block forever.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use schnorr::SchnorrAggregate;

// Any in-scope ciphersuite; Ristretto shown for illustration.
type C = ciphersuite::Ristretto;

#[test]
fn dos_huge_len_prefix() {
  // 4-byte prefix = ~4.29 billion claimed R points, then EOF.
  let mut bytes: &[u8] = &[0xFF, 0xFF, 0xFF, 0xFF];
  // Buffered case: loops billions of times before UnexpectedEof.
  let _ = SchnorrAggregate::<C>::read(&mut bytes);
}

#[test]
fn dos_blocking_reader_stall() {
  // A reader that yields the prefix then blocks forever (e.g., a socket
  // whose peer went silent) leaves read() parked inside read_exact
  // indefinitely — same primitive as the withheld TLS handshake.
  struct Drip;
  impl std::io::Read for Drip {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
      // First call returns the prefix; subsequent calls never return.
      std::thread::park();
      unreachable!()
    }
  }
  let mut drip = Drip;
  let _ = SchnorrAggregate::<C>::read(&mut drip); // never returns
}
```

Note: exploitation in the blocking form assumes the caller passes a blocking `io::Read` over a transport; the unbounded-loop/memory form requires only attacker-controlled bytes, matching the stated reachable surface for `read`-family APIs.

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
