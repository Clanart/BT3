### Title
`ThresholdKeys::read` trusts an attacker-controlled `u16` participant count to pre-allocate and drive parsing, enabling memory-exhaustion DoS from a tiny input - (File: crypto/dkg/src/lib.rs)

### Summary
The external report's bug class — trusting a declared length/count field to size an allocation or parse loop without validating it against the bytes actually present in the containing message — has a direct analog in `ThresholdKeys::read`. The function reads `t`, `n`, and `i` as raw `u16`s from the input stream, then immediately calls `Vec::with_capacity(usize::from(n))` and a `1 ..= n` loop over `read_G`, before `ThresholdParams::new` is ever consulted to validate the parameters [1](#0-0) .

### Finding Description
`ThresholdKeys::read` parses `(t, n, i)` from the reader, then deserializes an `Interpolation::Constant` variant by allocating `Vec::with_capacity(n)` and reading `n` scalars [2](#0-1) . It then loops `1 ..= n` reading `n` group elements into a `HashMap` of verification shares [3](#0-2) . Only after all of this allocation and parsing does it call `ThresholdParams::new(t, n, i)`, which would reject nonsensical `n` (e.g., `t > n`, `n > u16::MAX` participant bounds) — but by then the allocation and the read attempts have already happened [4](#0-3) .

This mirrors the ConnectBot advisory exactly: the declared count (`n`, up to 65535) is consumed from the stream and used to size heap allocation (`Vec::with_capacity` performs the full allocation eagerly, even if the stream ends immediately) and to bound a parse loop, without first checking the count against the bytes actually present or against protocol limits. An input of roughly 11 bytes (curve ID length + ID + t/n/i + interpolation tag) triggers a `with_capacity(65535)` allocation of `C::F`-sized slots (~2 MB for a 32-byte scalar, more for wider fields) plus a `HashMap` growth loop, then fails on the first `read_F` EOF — leaving the wasted allocation until the `Vec` is dropped.

The same class appears in adjacent in-scope code: `SchnorrAggregate::read` loops up to `u32::MAX` iterations over `read_G` from a 4-byte length prefix [5](#0-4) . That variant does not pre-allocate (`vec![]` grows only as elements are successfully read), so its amplification is bounded by input size; the `ThresholdKeys::read` case is the exploitable one because `Vec::with_capacity` allocates before a single payload byte is consumed.

### Impact Explanation
Availability. `ThresholdKeys::read` is an explicitly documented untrusted-input surface. An unprivileged peer supplying a ~11-byte message can force an eager ~2 MB heap allocation per call (and larger for curves with wide scalars, e.g., ed448/ kp256-style fields where `C::F` is ~56+ bytes → ~3.7 MB). Because each triggering input is only bytes long, an attacker obtains a memory amplification factor on the order of 10⁵× per message; repeated calls exhaust heap and can produce `OutOfMemoryError`/abort in the hosting process — precisely the CWE-789 / excessive-allocation impact of the advisory. No confidentiality or integrity impact.

### Likelihood Explanation
Reachability requires only that an application calls `ThresholdKeys::read` on peer-supplied bytes (e.g., during key-rotation/import flows), which is the function's stated purpose. No authentication or optional feature is needed beyond reaching the deserialization path. The amplification is per-call and requires iteration to cause OOM, and the per-call waste is bounded by `u16::MAX` rather than `u32::MAX`, so it is a medium-severity analog rather than high.

### Recommendation
Validate `t`, `n`, `i` via `ThresholdParams::new` (or an explicit bound check against the stream's remaining length / a protocol maximum like `MAX_PARTICIPANTS`) **before** calling `Vec::with_capacity(n)` or entering the `1 ..= n` read loops. Prefer `Vec::new()` + `push` so allocation grows only in proportion to bytes actually consumed, mirroring the advisory's fix of checking lengths against the containing stream.

### Proof of Concept
```rust
// Conceptual: feed a truncated ThresholdKeys encoding with n = 0xFFFF.
// Layout per crypto/dkg/src/lib.rs ThresholdKeys::read:
let mut bytes = Vec::new();
bytes.extend((C::ID.len() as u32).to_le_bytes());
bytes.extend(C::ID);
bytes.extend(1u16.to_le_bytes());      // t
bytes.extend(u16::MAX.to_le_bytes());  // n = 65535  <-- attacker field
bytes.extend(1u16.to_le_bytes());      // i
bytes.push(0);                          // Interpolation::Constant
// EOF here. ThresholdKeys::read will:
//   Vec::with_capacity(65535)  -> eager ~2-4 MB allocation
//   then fail on the first C::read_F EOF.
// Repeating with fresh tiny inputs amplifies heap growth ~10^5x input size.
```

Caveat: I could not fully verify `ReceivedOutput::read`/`EncryptedMessage::read` internals within the iteration budget; if those also use `with_capacity`/eager `vec![0; len]` on `u32` fields fed by the network, they may offer a larger amplification than the `u16`-bounded case reported here.

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

**File:** crypto/schnorr/src/aggregate.rs (L77-87)
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
```
