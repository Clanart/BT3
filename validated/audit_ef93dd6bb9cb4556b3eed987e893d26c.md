### Title
Unbounded attacker-controlled element count in `SchnorrAggregate::read` enables memory-exhaustion DoS - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::<C>::read` trusts a 4-byte little-endian `u32` element count and reads that many group elements into a `Vec<C::G>` with no upper bound, no sanity check, and no relationship to any expected signature count. An unprivileged party who can feed bytes to the aggregate-signature deserialization path (the same `read`/`verify` surface listed for `SchnorrSignature::read`) can force unbounded heap growth, mirroring the RakLib unbounded reliable-ordered queue (CWE-400) bug class.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, `SchnorrAggregate::read` does:

```rust
let mut len = [0; 4];
reader.read_exact(&mut len)?;
let mut Rs = vec![];
for _ in 0 .. u32::from_le_bytes(len) {
  Rs.push(C::read_G(reader)?);
}
``` [1](#0-0) 

- `len` can be up to `u32::MAX` (~4.29 billion). For a 32-byte-point ciphersuite that is ~137 GB of retained allocations, plus `Vec` growth overhead — the count is never compared to anything.
- `C::read_G` performs full point decompression/validation per element, so the loop also burns CPU proportional to the claimed/supplied count before any signature semantics are checked.
- The resulting `SchnorrAggregate` is then verified by `SchnorrAggregate::verify`, which does `Vec::with_capacity((2 * keys_and_challenges.len()) + 1)` and a `multiexp_vartime` over the attacker-influenced number of pairs — a second, amplified allocation and multiexponentiation whose size is only bounded by the attacker-controlled `Rs` length (it must equal `keys_and_challenges.len()`, but that list is also derived from attacker-presented signatures in any aggregation use case). [2](#0-1) 

The codebase shows awareness of this exact bug class elsewhere — `networks/ethereum/src/machine.rs` `Call::read` explicitly comments "A valid DoS would be to claim a 4 GB data is present" and chunks its read — but no such mitigation exists in `SchnorrAggregate::read`. Contrast with `dkg`'s `Participant`/`ThresholdParams`, which bound counts to `u16`/`n` at construction, and `Commitments::read` in PedPoP, which sizes the vector from `params.t()` rather than from wire data. [3](#0-2) 

### Impact Explanation
Any code path that deserializes an aggregate Schnorr signature from untrusted bytes (e.g., `SchnorrAggregate::read` on a `&[u8]` or stream supplied by a peer) lets an attacker drive heap usage to process-exhausting levels and/or force a massive multiexponentiation in `verify`. This is a pure availability impact (CWE-400), matching the Medium severity of the referenced advisory: no key material is leaked and no signature is forged, but the victim process can be OOM-killed or stalled. Impact is bounded by how much data the attacker can actually stream (memory grows roughly linearly with bytes sent since `Rs` is built incrementally), which is why this is Medium rather than High — it requires transmitting data proportional to the memory consumed, though the retained allocation outlives the read, just like the RakLib queue retaining packets until the missing sequence number arrives.

### Likelihood Explanation
Reachable whenever `SchnorrAggregate` deserialization is exposed to remote input — the crate exposes `read`/`serialize` as public API exactly for wire use. Exploitation is trivial: a 4-byte `0xFFFFFFFF` prefix followed by a stream of validly-encoded group elements (or even a `Read` implementation that never terminates) grows the `Vec` without limit. No threshold of malicious parties, no valid signatures, and no cryptographic capability is required — only bytes. `write` even documents the domain ("more than 4 billion signatures"), confirming counts near `u32::MAX` are within the format's expressible range yet unvalidated on read. [4](#0-3) 

### Recommendation
Bound the count before allocating/reading: reject `len` greater than a protocol-appropriate maximum (e.g., the number of signatures actually being aggregated, or a hard cap), before the loop — e.g., `if len > MAX_AGGREGATE_SIGS { return Err(...) }`. Optionally also pass an expected-count parameter to `read` so deserialization is tied to context, the same way `Commitments::read` sizes itself from `params.t()`. If `usize` could ever be 32-bit, also guard the `2 * len + 1` capacity computation in `verify` against overflow.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Ciphersuite; // e.g., a ristretto/secp256k1 ciphersuite as C
use schnorr::aggregate::SchnorrAggregate;

// Attacker-controlled bytes: claim u32::MAX aggregate members.
let mut bytes = u32::MAX.to_le_bytes().to_vec();
// Append valid point encodings as long as desired; the Vec grows
// for every element read with no cap on `len`.
bytes.extend_from_slice(C::generator().to_bytes().as_ref());
// ... repeat generator bytes N times, or stream them indefinitely ...

// Each call retains every decoded point in memory until the reader errors.
let _ = SchnorrAggregate::<C>::read(&mut Cursor::new(&bytes));
```
With a streaming `Read` (network socket), the loop in `crypto/schnorr/src/aggregate.rs:83-85` retains every decoded `C::G` for the lifetime of the read — up to ~4.29 billion points (~137 GB for 32-byte encodings) — and, on completion, `verify` allocates `2 * len + 1` pairs and runs a `multiexp_vartime` over them, compounding memory with CPU exhaustion.

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

**File:** crypto/schnorr/src/aggregate.rs (L90-104)
```rust
  /// Write a SchnorrAggregate to something implementing Write.
  ///
  /// This will panic if more than 4 billion signatures were aggregated.
  pub fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(
      &u32::try_from(self.Rs.len())
        .expect("more than 4 billion signatures in aggregate")
        .to_le_bytes(),
    )?;
    #[allow(non_snake_case)]
    for R in &self.Rs {
      writer.write_all(R.to_bytes().as_ref())?;
    }
    writer.write_all(self.s.to_repr().as_ref())
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L127-145)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }

    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```

**File:** crypto/dkg/pedpop/src/lib.rs (L110-128)
```rust
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```
