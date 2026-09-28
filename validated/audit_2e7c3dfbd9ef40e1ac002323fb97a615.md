### Title
`ThresholdKeys::read` performs attacker-controlled pre-allocation of the `Constant` interpolation vector before validating input size - (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes uncontrolled resource consumption driven by untrusted length fields. The reachable analog in Serai's in-scope code is `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`: an attacker-controlled `n` field (a raw `u16` read directly from the byte stream) is used to drive `Vec::with_capacity(usize::from(n))` when deserializing `Interpolation::Constant`, allocating memory proportional to an untrusted value before any element is actually read or the parameters are validated.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw `u16`s straight from the reader [1](#0-0) . When the interpolation tag is `0` (`Constant`), it immediately executes `Vec::with_capacity(usize::from(n))` for `n` field elements — up to 65,535 elements, roughly 2 MB per call for a 32-byte field — driven purely by two bytes of attacker input [2](#0-1) . The allocation happens before `ThresholdParams::new` validates `t <= n` and before the subsequent `C::read_F`/`read_G` loop consumes bytes [3](#0-2) . There is no pre-check bounding `n` against the actual length of the supplied buffer, so a ~10-byte input forces a megabyte-scale allocation. This is the same shape as the reported bug class (untrusted length field → disproportionate resource use), reachable via the explicitly in-scope `ThresholdKeys::read` entry point for untrusted bytes.

The related `SchnorrAggregate::read` reads a `u32` count but grows `Rs` incrementally via `push`/`read_G`, so memory stays proportional to bytes actually supplied and it is not a real analog [4](#0-3) .

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (key-share material untrusted bytes are fed to per the protocol surface) causes each call to allocate up to ~2 MB from a handful of input bytes. Issued repeatedly or concurrently, this provides a memory-amplification denial-of-service vector against the process without the attacker supplying proportional bandwidth. The allocation is transient (dropped on the subsequent `read_F` failure), so impact is resource-consumption DoS rather than persistent corruption; severity is bounded by the `u16` cap on `n`.

### Likelihood Explanation
Any interface that deserializes `ThresholdKeys` from untrusted bytes reaches this path; the `Constant` interpolation branch requires only tag byte `0` plus a large `n`. The amplification factor (~2 MB per tiny message) is meaningful for memory pressure but capped at 65,535 field elements per call, so exploitation requires sustained request volume. Likelihood is moderate wherever `ThresholdKeys::read` is exposed to unauthenticated input.

### Recommendation
Do not pre-size the vector from the untrusted `n`. Either cap `n` before allocating (e.g., reject `n` above a sane protocol maximum early, since `ThresholdParams::new(t, n, i)` validation happens only after the reads), or grow the `Vec` incrementally with `Vec::new()` + `push` inside the loop so memory tracks actual bytes consumed, matching the pattern used in `SchnorrAggregate::read`. Reorder so `ThresholdParams` validation occurs before any allocation driven by `n`.

### Proof of Concept
Feed `ThresholdKeys::read` a buffer consisting of: a valid `C::ID` length + `C::ID`, `t = 0x0001`, `n = 0xffff`, `i = 0x0001`, and interpolation tag `0x00`, then truncate the input. The call executes `Vec::with_capacity(65535)` for `C::F` (~2 MB allocation) before `C::read_F` hits EOF and errors out. Each such ~40-byte message forces a megabyte-scale allocation; N concurrent calls multiply memory pressure by N.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-602)
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
```

**File:** crypto/dkg/src/lib.rs (L604-613)
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
```

**File:** crypto/dkg/src/lib.rs (L620-631)
```rust
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
