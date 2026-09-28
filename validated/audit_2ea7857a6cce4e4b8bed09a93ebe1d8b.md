### Title
Uncontrolled resource consumption via attacker-controlled participant count in `ThresholdKeys::read` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The deserialization path for `ThresholdKeys` allocates collections sized by an attacker-controlled `n` field before validating the threshold parameters, allowing an unprivileged party supplying crafted bytes to trigger disproportionate memory allocation per request — the same resource-consumption-without-quota class as CVE-2022-3277.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw `u16` values from the reader without any bound checks at parse time [1](#0-0) . When the interpolation tag is `0` (`Interpolation::Constant`), it immediately executes `Vec::with_capacity(usize::from(n))` and then attempts `n` calls to `C::read_F` [2](#0-1) . Independently of the interpolation variant, it then loops `1 ..= n` inserting into a `HashMap` of verification shares via `C::read_G` [3](#0-2) . Only after all of this allocation and parsing does `ThresholdParams::new(t, n, i)` get invoked to actually validate the parameters [4](#0-3) .

This means a reader sees roughly 11 bytes of input (ID length + ID + t/n/i + tag) and has already committed a `Vec` allocation of `65535 * size_of::<C::F>()` (~2 MB for 32-byte fields) plus up to 65,535 scalar-read attempts, entirely driven by a 2-byte attacker-controlled field with no quota or size relationship to the actual bytes supplied.

### Impact Explanation
Any component that feeds untrusted bytes into `ThresholdKeys::read` (explicitly part of the reachable input surface) can be forced to allocate ~2 MB and perform tens of thousands of field/point deserialization attempts per request. Repeated requests, or concurrent deserializations, cause unbounded memory pressure and CPU burn unconstrained by the attacker's actual bandwidth expenditure — a denial of service matching the reported class. This differs from the correctly bounded `Vec::with_capacity` uses elsewhere (e.g., `SchnorrAggregate::read` pushes per point actually read; `coordinator/tributary` transactions check `TRANSACTION_SIZE_LIMIT` before allocating [5](#0-4) ).

### Likelihood Explanation
`n` is fully attacker-controlled: it is two bytes in the serialized stream and `u16::MAX` requires no validity until `ThresholdParams::new` runs at the end of `read`. No authentication, proof, or signature gates the allocation — it occurs purely during parsing. The only mitigations are the implicit `u16` ceiling (capping the per-call amplification at ~65,535 elements) and allocator failure behavior; there is no protocol-level bound tying declared `n` to bytes actually present.

### Recommendation
Validate `t`/`n`/`i` by constructing `ThresholdParams` immediately after reading them, before any `n`-sized allocation or loop. Reject `n` values inconsistent with the remaining input length, and replace `Vec::with_capacity(n)` with incremental `push` (or a sanity cap such as `n <= 256`, the realistic validator-set scale) so declared counts cannot create resources disproportionate to supplied bytes.

### Proof of Concept
Feed `ThresholdKeys::read::<&[u8]>` a buffer containing:
1. `u32` length equal to `C::ID.len()` followed by the correct `C::ID`,
2. `t = 0xFFFF`, `n = 0xFFFF`, `i = 0x0001` (little-endian `u16`s),
3. interpolation tag `0x00` (Constant),
4. no further bytes.

`read` executes `Vec::with_capacity(65535)` (~2 MB committed) and enters the `read_F` loop before any `ThresholdParams::new` validation or EOF check rejects the input. Repeat this in a loop to grow allocator pressure; each iteration costs the attacker ~12 transmitted bytes.

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

**File:** crypto/dkg/src/lib.rs (L604-616)
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
```

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/dkg/src/lib.rs (L625-631)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```

**File:** coordinator/src/tributary/transaction.rs (L283-295)
```rust
          let mut each_commitments_len = [0; 2];
          reader.read_exact(&mut each_commitments_len)?;
          let each_commitments_len = usize::from(u16::from_le_bytes(each_commitments_len));
          if (commitments_len * each_commitments_len) > TRANSACTION_SIZE_LIMIT {
            Err(io::Error::other(
              "commitments present in transaction exceeded transaction size limit",
            ))?;
          }
          let mut commitments = vec![vec![]; commitments_len];
          for commitments in &mut commitments {
            *commitments = vec![0; each_commitments_len];
            reader.read_exact(commitments)?;
          }
```
