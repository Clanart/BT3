### Title
Unbounded length-prefixed read in `SchnorrAggregate::read` allows memory/CPU denial of service from attacker-controlled bytes - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` reads a little-endian `u32` count from an untrusted reader and then loops that many times, pushing each deserialized group element into a `Vec` with no cap on the count. A public input consisting of a large declared length followed by a long stream of valid point encodings forces allocation of up to ~4 billion `C::G` elements (and the equivalent amount of decompression work), exhausting memory/CPU. This is the Serai analog of CVE-2024-1062: a size field trusted without a bound, causing a crash-level denial of service on oversized input.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, `SchnorrAggregate::read` reads `len` as a `u32` and iterates `0 .. u32::from_le_bytes(len)`, calling `C::read_G(reader)` per iteration and pushing into an unbounded `Vec` [1](#0-0) . Contrast with `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`, which derives its loop bound from `n: u16` (max 65,535) and additionally validates it through `ThresholdParams::new`/`ThresholdKeys::new` [2](#0-1) . No analogous bound exists for the aggregate count: the only limit is `u32::MAX`, and `read_G` performs full point decompression and canonicality re-checking per element [3](#0-2) .

### Impact Explanation
`SchnorrAggregate::read` is in the explicitly in-scope set of untrusted-byte decoders (`SchnorrSignature::read`/aggregate verification inputs). Any component that deserializes an aggregate signature supplied by an external party (e.g., half-aggregated Schnorr signatures attached to messages/transactions) can be driven into:
- **Memory exhaustion:** each retained `C::G` occupies heap memory in `Rs`; a stream of valid encodings lets an attacker approach ~4 billion entries, far exceeding available RAM (OOM kill of the signer/verifier process).
- **CPU exhaustion:** each iteration performs a full point decompression plus a re-encode canonicality comparison in `read_G`, so even modest input sizes impose disproportionate cost before the stream ends.

Result: remote denial of service of the signing/verification process — the same impact class (availability loss from an oversized value) as the reference CVE, rated Medium.

### Likelihood Explanation
Reachability requires only that some integration feeds attacker-controlled bytes into `SchnorrAggregate::read` — precisely the untrusted-decoder surface the rules enumerate. No key material, validator status, or collusion is needed: the attacker just needs to deliver a byte string beginning with a large `u32` length followed by as many valid point encodings as they can transmit. Cost to the attacker is linear in bytes sent; cost to the victim is linear in decompression work plus retained allocations until stream end. In a no_std/`alloc` context the growth of `Rs` will abort on allocation failure; under `std` it panics/aborts on OOM.

### Recommendation
Cap the aggregate count before reading:
- Enforce a protocol-appropriate maximum (e.g., the multisig's `n`, or a hard constant) immediately after reading `len`, erroring with `io::Error` if exceeded.
- Alternatively pre-check `reader` length / use `Vec::with_capacity` only after the bound check so a huge `len` cannot drive unbounded allocation.
- Optionally stream-verify `Rs` against `keys_and_challenges.len()` consistency (verify already rejects mismatched lengths at `aggregate.rs:128`, so a tighter early bound loses no functionality).

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Ciphersuite;
use schnorr::aggregate::SchnorrAggregate;

// SeCp256k1 point encoding is 33 bytes; any in-scope suite works.
fn dos() -> std::io::Result<()> {
  let mut bytes = vec![];
  // Declared count: u32::MAX
  bytes.extend_from_slice(&u32::MAX.to_le_bytes());
  // Follow with a long stream of valid compressed generator encodings.
  // Each iteration allocates one C::G and performs full decompression;
  // the Vec grows until OOM/abort.
  let gen = <Secp256k1 as Ciphersuite>::generator().to_bytes();
  while { bytes.extend_from_slice(gen.as_ref()); bytes.len() < (1 << 30) } {}
  SchnorrAggregate::<Secp256k1>::read(&mut Cursor::new(bytes)).map(|_| ())
}
```
With a `u32::MAX` prefix and a stream of valid encodings, `read` loops/allocates until memory is exhausted; even a truncated stream forces decompression work proportional to attacker-sent bytes with no semantic upper bound.

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

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
