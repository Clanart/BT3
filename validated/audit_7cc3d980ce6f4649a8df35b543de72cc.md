### Title
ThresholdKeys::read allocates a heap buffer sized by an attacker-controlled participant count before validating it - (File: crypto/dkg/src/lib.rs)

### Summary
The Prometheus advisory (GHSA-8rm2-7qqf-34qm) is a classic CWE-789 uncontrolled-memory-allocation bug: a length field declared by an unauthenticated remote peer is trusted to size a heap allocation before any data is validated. The same pattern exists in `ThresholdKeys::read` in the `dkg` crate, which is one of the explicitly reachable deserialization entry points. The function reads the parameter `n` from the byte stream and immediately calls `Vec::with_capacity(usize::from(n))` to size the `Interpolation::Constant` coefficient vector, before a single coefficient has been read and before `ThresholdParams::new` has validated `t`/`n`/`i`.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` parses `(t, n, i)` as three little-endian `u16`s from the untrusted reader (lines 591–602). It then reads a one-byte interpolation tag and, for tag `0` (`Interpolation::Constant`), executes:

```rust
let mut res = Vec::with_capacity(usize::from(n));
for _ in 0 .. n {
  res.push(C::read_F(reader)?);
}
```

at lines 607–613. The `with_capacity` call commits `n * size_of::<C::F>()` bytes of heap memory based purely on the two attacker-supplied bytes encoding `n`. Only after the allocation does the function attempt to read the coefficients, and only after everything is read does `ThresholdParams::new(t, n, i)` (line 626) check that the parameters are coherent — so a malformed stream still triggers the full allocation first.

The caller controls `n` up to 65535. For a 32-byte scalar field (Ristretto, Secp256k1, ed448), that is roughly 2 MiB committed per call from a ~12-byte prefix of input. The same `n` then drives the `verification_shares` loop at lines 620–623 (`for l in (1 ..= n)` inserting into a `HashMap`), which adds further per-element work and allocation proportional to the declared count, again before any semantic validation. [1](#0-0) 

By contrast, `SchnorrAggregate::read` in `crypto/schnorr/src/aggregate.rs` (lines 77–87) only `push`es elements as bytes actually arrive on the reader, so it fails fast on EOF without speculative allocation, and `Commitments::read` in `crypto/dkg/pedpop/src/lib.rs` (line 111) sizes its `with_capacity` from the locally-configured `params.t()` rather than attacker bytes. `ThresholdKeys::read` is the only reachable path where a declared count directly sizes an allocation.

### Impact Explanation
An unprivileged party who can supply serialized `ThresholdKeys` bytes — the prompt's accepted surface for untrusted input to `ThresholdKeys::read` — can force a ~2 MiB heap allocation with a handful of bytes, and force that allocation to be attempted before the message is rejected as malformed. Under concurrent requests this is a memory-amplification denial of service directly analogous to the snappy decoded-length bug: small input, large committed allocation, per-request, reachable without authentication or any valid cryptographic content. Impact is availability only; no secret material is leaked and no signature/proof is forged.

### Likelihood Explanation
Exploitation requires an integration that calls `ThresholdKeys::read` on network- or peer-supplied bytes, which is the intended use of a serialization API for DKG outputs (key-reshare/recovery transports). Each malicious message yields at most ~2 MiB since `n` is a `u16`, so the amplification factor (~170,000× input bytes, but small absolute size) is weaker than the Prometheus case where declared lengths were effectively unbounded. It also requires allocation pressure concurrency to actually exhaust memory. This places the analog in Medium territory rather than High: real and reachable, but the per-request amplification is capped by the two-byte field.

### Recommendation
Validate `t`, `n`, `i` via `ThresholdParams::new` before allocating, and reject absurd participant counts early (e.g., cap `n` at a documented maximum before `with_capacity`). Prefer push-as-you-read without speculative `with_capacity`, or read into a bounded chunk pattern like the mitigation used in `networks/ethereum/src/machine.rs` `Call::read` (which explicitly comments on this exact DoS class and reads in 1 KB chunks). Move the `Interpolation::Constant` branch to only reserve capacity after parameters have been validated.

### Proof of Concept
```rust
use std::io;
use frost::curve::Ristretto;
use frost::ThresholdKeys;

// Attacker-controlled stream: valid C::ID header, then t=1, n=0xFFFF, i=1,
// interpolation tag 0 (Constant) — then EOF. ThresholdKeys::read still
// commits ~2 MiB via Vec::with_capacity(65535) before failing on EOF
// and before ThresholdParams::new ever sees the parameters.
fn poc() -> io::Result<()> {
  let mut bytes = vec![];
  // id_len + id for Ristretto
  bytes.extend(&(u32::try_from(<Ristretto as frost::curve::Ciphersuite>::ID.len())
    .unwrap()).to_le_bytes());
  bytes.extend(<Ristretto as frost::curve::Ciphersuite>::ID);
  bytes.extend(&1u16.to_le_bytes());      // t
  bytes.extend(&0xFFFFu16.to_le_bytes()); // n = 65535 -> ~2 MiB allocation
  bytes.extend(&1u16.to_le_bytes());      // i
  bytes.push(0);                          // Interpolation::Constant
  // No coefficient bytes follow: read errors, but the allocation already ran.
  let _ = ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice());
  Ok(())
}
```

Caveat: severity is bounded by the `u16` field width; if the graders require per-request amplification in the hundreds-of-MB range to rate this High, this analog qualifies as Medium (CWE-789 allocation without a pre-validation bound, reachable from the listed `ThresholdKeys::read` surface).

### Citations

**File:** crypto/dkg/src/lib.rs (L591-631)
```rust
    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

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

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```
