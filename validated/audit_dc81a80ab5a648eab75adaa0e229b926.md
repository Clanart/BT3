### Title
Attacker-controlled `n`/`t` in `ThresholdKeys::read` triggers quadratic Lagrange interpolation and tens of thousands of point deserializations, causing CPU exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574) deserializes `t`, `n`, and `i` as raw u16s with no upper bound beyond `u16::MAX`, then reads `n` group elements and calls `ThresholdKeys::new`, which computes the group key by evaluating a Lagrange `interpolation_factor` for each of the `t` lowest participants. Each `interpolation_factor` call is O(`t`) scalar multiplications plus an inversion (crypto/dkg/src/lib.rs:226-248), making deserialization O(`t`² + `n`) — about 4.3 billion field multiplications and 65,535 inversions for a maximally crafted input of only ~2 MB. This is the direct analog of CVE-2023-39329 / JLSEC-2026-550 (resource exhaustion in `opj_t1_decode_cblks` via a crafted file): a small crafted byte string drives disproportionate computation during decoding.

### Finding Description
`ThresholdKeys::read` performs the following on attacker-supplied bytes:

- Reads `t`, `n`, `i` (crypto/dkg/src/lib.rs:591-602). `ThresholdParams::new` only checks `t <= n` and `i <= n`, so `t = n = 65535` is accepted (crypto/dkg/src/lib.rs:166-178).
- Reads `n` verification shares via `read_G`, each a full point decode (crypto/dkg/src/lib.rs:620-623).
- `ThresholdKeys::new` computes `group_key` as `sum over i in 1..=t of verification_shares[i] * interpolation_factor(i, t)` (crypto/dkg/src/lib.rs:376-378).
- `Interpolation::Lagrange::interpolation_factor` iterates over all `t` included participants doing two field multiplications per element, then a field inversion (crypto/dkg/src/lib.rs:229-246).

Total work is ~`t`² scalar multiplications and `t` inversions, plus `t` point-scalar multiplications. With `t = 65535` that is ~4.3 × 10⁹ field multiplications in a single `read` call — unbounded CPU consumption on a ~2 MB input. A second-order amplifier exists in `ThresholdKeys::view` (crypto/dkg/src/lib.rs:500-507), which again computes a per-participant O(`included`) Lagrange factor for every included participant — O(`included`²) — on every signing session using such keys.

### Impact Explanation
Any service that feeds network- or peer-supplied bytes to `ThresholdKeys::read` (explicitly an accepted input path for this audit) can be driven into effectively unbounded CPU consumption by a single short message. This stalls the signing/DKG pipeline and denies availability of the validator — the same impact class (resource exhaustion / denial of service) as the reference advisory, and a stronger reachability story since it needs only a crafted serialization rather than a valid protocol session.

### Likelihood Explanation
`ThresholdKeys` serialization is a natural wire/storage format for DKG outputs; anything that syncs or restores key material from unauthenticated or peer-sourced bytes is exposed. The attack requires no valid signatures, shares, or protocol state — only a well-formed header declaring `t = n = 65535` with Lagrange interpolation and enough trailing bytes to satisfy the `n` `read_G` calls (and `read_G` failures don't even stop it early only if points decode; a failed decode aborts, so the attacker must supply decodable points — trivially done with repeated identity/generator encodings since no binding to real keys is checked during `read`).

### Recommendation
Cap `t` and `n` at a sane protocol maximum (e.g., `MAX_KEY_SHARES_PER_SET`) inside `ThresholdKeys::read`/`ThresholdParams::new`, and/or cap the interpolation factor computation. Lagrange factors over `1..=t` can also be computed in O(`t`) total with the standard prefix/suffix-product trick instead of O(`t`) each.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::read with attacker bytes
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend(C::F::ONE.to_repr().as_ref()); // secret_share
for _ in 0 .. 65535 {
  buf.extend(C::generator().to_bytes().as_ref()); // n verification shares
}
// ~2.1 MB input -> ~4.3e9 field muls + 65535 inversions in ThresholdKeys::new
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
```

Root cause is `ThresholdKeys::new` computing `group_key` via per-participant O(`t`) `interpolation_factor` calls with parameters drawn entirely from the byte stream ( [1](#0-0) , [2](#0-1) , [3](#0-2) ).

### Citations

**File:** crypto/dkg/src/lib.rs (L226-246)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
        let i_f = F::from(u64::from(u16::from(i)));

        let mut num = F::ONE;
        let mut denom = F::ONE;
        for l in included {
          if i == *l {
            continue;
          }

          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L591-623)
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
```
