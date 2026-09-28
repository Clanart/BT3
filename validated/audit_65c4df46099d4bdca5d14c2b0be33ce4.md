### Title
Untrusted `n` in `ThresholdKeys::read` drives unvalidated `Vec::with_capacity`, enabling memory exhaustion - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` reads the participant count `n` (a raw `u16`) from an attacker-controlled byte stream and immediately uses it to size a `Vec::with_capacity(usize::from(n))` and a `1 ..= n` read loop — before `ThresholdParams::new` validates `t`/`n`/`i`. A handful of crafted bytes therefore forces an allocation of up to 65,535 scalar slots (~2 MiB per call for 32-byte fields) plus a HashMap-fill loop, analogous to GHSA-87x9-7grx-m28v's attacker-driven allocation growth in `notation-go` verification. `ThresholdKeys::read` is a reachable unprivileged deserialization entry point.

### Finding Description
In `ThresholdKeys::read` ( [1](#0-0) ), `t`, `n`, and `i` are read directly from the reader as raw `u16`s with no validation. The interpolation tag byte is then read; for tag `0` (`Interpolation::Constant`), the code does:

```rust
let mut res = Vec::with_capacity(usize::from(n));
for _ in 0 .. n {
  res.push(C::read_F(reader)?);
}
```

and afterwards reads `n` verification shares into a `HashMap` ( [2](#0-1) ). Only at the end does `ThresholdKeys::new(ThresholdParams::new(t, n, i), ...)` perform semantic validation ( [3](#0-2) ) — after the allocation and read loop have already run. There is no bound tying `n` to the remaining bytes in the stream or to any sane maximum; an invalid `t > n`, zero `n` is excluded but `n = 0xFFFF` is fully processed regardless of whether the caller ever intended `Constant` interpolation (writers only emit `Constant` when `t == n`, but readers don't check that until too late).

`C::read_F`/`C::read_G` read fixed-size encodings and fail on EOF ( [4](#0-3) ), so the loop terminates early on a short input — but the `Vec::with_capacity` itself reserves `n × size_of::<F>()` bytes unconditionally, and each element actually supplied is consumed before failure. The amplification is ~`11` input bytes → `~2 MiB` committed capacity per call, repeatable as fast as the attacker can feed the reader.

### Impact Explanation
Availability loss: any service that deserializes `ThresholdKeys` from untrusted bytes (e.g., key material exchange, recovery flows, or anything calling `ThresholdKeys::read` on network/peer-supplied data — the same trust class as `read_preprocess`/`read_share`) can be driven to excessive memory allocation. Repeated invocations with a `0` interpolation tag and `n = 0xFFFF` force ~2 MiB allocations each plus partial hashmap fills, mirroring the `notation-go` advisory where verification of attacker input caused unbounded memory growth until the process was killed (CWE-770, availability-only, CVSS High). The cap of `u16::MAX` bounds per-call cost, but the per-call cost-to-attacker-input ratio (~10^5×) and unlimited repetition keep this a genuine resource-exhaustion vector rather than a no-impact one.

### Likelihood Explanation
Moderate. Exploitability requires a path where an unprivileged party controls bytes passed to `ThresholdKeys::read`; such material is normally authenticated, lowering likelihood versus a raw verification DoS. However, no authentication check exists inside the function itself, the allocation precedes all validation, and the trigger bytes are trivial (`id_len || id || t || n=0xFFFF || i || 0x00`). Whether production callers expose this over an unauthenticated surface is not verifiable from the in-scope crate alone.

### Recommendation
Validate `t`, `n`, `i` via `ThresholdParams::new` **before** allocating, and reject `n` values inconsistent with the remaining stream length before `Vec::with_capacity`. Additionally, cap `n` at the maximum participant count the deployment supports, and require `Constant` interpolation only when `t == n` (as `ThresholdKeys::new` enforces at [5](#0-4) ) prior to reading coefficients — or prefer `Vec::new()` and let it grow proportionally to bytes actually consumed.

### Proof of Concept
```rust
// Attacker-controlled input to ThresholdKeys::<C>::read
let mut bytes = vec![];
bytes.extend(&(C::ID.len() as u32).to_le_bytes()); // id_len
bytes.extend(C::ID);                              // id
bytes.extend(&1u16.to_le_bytes());                // t = 1
bytes.extend(&u16::MAX.to_le_bytes());            // n = 65535 (unvalidated)
bytes.extend(&1u16.to_le_bytes());                // i = 1
bytes.push(0u8);                                  // Interpolation::Constant
// ~11 + ID.len() bytes => Vec::with_capacity(65535) scalars allocated (~2 MiB
// for a 32-byte field element) before any params validation occurs.
let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
// Repeat in a loop to exhaust memory; each iteration is sub-20-byte cost to
// the attacker and multi-MiB committed capacity to the victim.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L367-374)
```rust
    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }
```

**File:** crypto/dkg/src/lib.rs (L591-616)
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
```

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/dkg/src/lib.rs (L625-632)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L74-83)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }
```
