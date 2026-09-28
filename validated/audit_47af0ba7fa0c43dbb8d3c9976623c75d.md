### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` reachable from a ~2 MB untrusted blob via `ThresholdKeys::read` enables CPU-exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` accepts attacker-controlled `t`/`n` parameters (each a raw `u16`, up to 65535) and then calls `ThresholdKeys::new`, which computes `group_key` by evaluating `interpolation_factor` for every participant `1..=t`. `Interpolation::Lagrange::interpolation_factor` is itself O(|included|) field work plus a field inversion, so `ThresholdKeys::new` performs O(t²) scalar multiplications and t field inversions, plus t full scalar–point multiplications for the group-key sum. With `t = n = 65535`, a ~2.1 MB serialized blob triggers ~4.3×10⁹ field multiplications and ~65k scalar inversions and ~65k point multiplications — a compute-to-input amplification on the order of 10³–10⁴×, with no cap enforced before the work is done.

### Finding Description
The deserialization path is:

- `ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s from the stream with no upper bound beyond `u16::MAX` [1](#0-0) 
- It reads `n` scalars/points (`n × 32–57` bytes — small) and calls `ThresholdKeys::new` [2](#0-1) 
- `ThresholdKeys::new` computes the group key as `sum(verification_shares[i] * interpolation_factor(i, 1..=t))` over all `t` participants [3](#0-2) 
- `Interpolation::Lagrange::interpolation_factor` loops over the entire `included` slice doing two field multiplications per element and a `denom.invert()` per call [4](#0-3) 

So with `Interpolation::Lagrange` selected (single `0x01` byte), `t = n = 65535`, `i = 1`, the caller must supply only ~`65535 × 32 ≈ 2.1 MB` of points (for Ristretto/Ed25519), and `ThresholdKeys::new` will perform ~4.3 billion field multiplications, 65535 batched-independent field inversions, and 65535 point scalar-muls before returning. There is no sanity check limiting `n`/`t` to protocol-realistic committee sizes anywhere in `read` or `new` — `ThresholdParams::new` only checks `t <= n` and `i <= n` [5](#0-4) .

The same quadratic fan-out also exists on the signing path: `view()` calls `interpolation_factor` once per included participant [6](#0-5) , but the read path is the strongest because it requires no prior key material at all — just attacker-controlled bytes handed to `ThresholdKeys::read`.

### Impact Explanation
An unprivileged party who can get a Serai component (or any gateway/service embedding these crates) to deserialize `ThresholdKeys` from untrusted bytes — the exact class of input the scope rules allow for `ThresholdKeys::read` — can pin a CPU core for an extended period per ~2 MB message. Repeating the request monopolizes the target's processing capacity, degrading or halting signing/verification throughput for legitimate users, mirroring the CVE-2025-70957 pattern of disproportionate computation for small inputs.

### Likelihood Explanation
`ThresholdKeys::read`/`write` are the canonical serialization for threshold key material in the DKG crate and are used by tooling that round-trips keys through byte streams. Any component that accepts these bytes from a remote or not-fully-trusted source (key-delivery, recovery, backup-restore endpoints) exposes the path. The attacker only needs to craft the byte stream; no keys, no stake, no collusion.

### Recommendation
Enforce a protocol-level maximum on `n`/`t` inside `ThresholdKeys::read` (and `ThresholdParams::new`) — e.g., reject `n` above the largest validator set the protocol supports — before allocating or interpolating. Additionally, `group_key` computation can be made O(n log n) or done with a single inversion via prefix/suffix products instead of one inversion per factor.

### Proof of Concept
```rust
// Builds the minimal serialization ThresholdKeys::read accepts (Ristretto example),
// with t = n = 65535, i = 1, Lagrange interpolation, and n identity points.
fn dos_blob() -> Vec<u8> {
    let mut b = vec![];
    b.extend((C::ID.len() as u32).to_le_bytes()); // id_len
    b.extend(C::ID);                              // id
    b.extend(65535u16.to_le_bytes());             // t
    b.extend(65535u16.to_le_bytes());             // n
    b.extend(1u16.to_le_bytes());                 // i
    b.push(1);                                    // Interpolation::Lagrange
    b.extend(<C as Ciphersuite>::F::ZERO.to_repr().as_ref()); // secret_share
    for _ in 0 .. 65535 {
        b.extend(C::G::identity().to_bytes().as_ref()); // verification_shares
    }
    b // ~2.1 MB for 32-byte points
}

// Triggers O(t^2) ≈ 4.3e9 field muls + 65535 inversions + 65535 point muls:
let _keys = ThresholdKeys::<C>::read(&mut dos_blob().as_slice()).unwrap();
```
`ThresholdKeys::new` does not reject the extreme parameters, and the `1..=t` sum over O(n)-cost `interpolation_factor` calls performs the quadratic work before returning.

### Citations

**File:** crypto/dkg/src/lib.rs (L166-179)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }

    if t > n {
      return Err(DkgError::InvalidThreshold { t, n });
    }
    if i.0 > n {
      return Err(DkgError::InvalidParticipant { n, participant: i });
    }

    Ok(ThresholdParams { t, n, i })
  }
```

**File:** crypto/dkg/src/lib.rs (L226-247)
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
      }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L500-507)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
```

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
