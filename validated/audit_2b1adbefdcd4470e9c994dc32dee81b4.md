### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` enables CPU-starvation DoS via untrusted `ThresholdKeys::read` bytes - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` accepts an attacker-controlled `t`/`n` (each a `u16`, up to 65535) and calls `ThresholdKeys::new`, which computes the group key by evaluating `Interpolation::Lagrange::interpolation_factor` once per participant in `1..=t`. Each evaluation is `O(t)` field operations plus an inversion, making deserialization `O(t²)` — roughly 4.3·10⁹ field multiplications and 65 535 inversions for `t = n = 65535` — for a serialized input of only ~2 MB. This is a CWE-400/CWE-770 unbounded-resource-consumption analog of the Synapse CPU-starvation advisory.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the stream with only `ThresholdParams::new` validating `t <= n` — both are `u16`, so up to 65535 [1](#0-0) . It then reads `n` verification shares and calls `ThresholdKeys::new` [2](#0-1) .

`ThresholdKeys::new` computes the group key by summing `verification_shares[i] * interpolation_factor(i, t)` for `i` in `1..=t` [3](#0-2) . For `Interpolation::Lagrange` (tag byte `1`, requiring no `t == n` restriction), each `interpolation_factor` call iterates over all `t` included participants performing two field multiplications each, plus one field inversion [4](#0-3) . Total work is `Θ(t²)` field multiplications and `Θ(t)` inversions — while the attacker's input is only `Θ(n)` bytes (~2.1 MB at `n = 65535`, 32-byte points).

The same quadratic pattern recurs at sign time: `ThresholdKeys::view` calls `interpolation_factor` for every included signer [5](#0-4) , and `AlgorithmSignMachine::sign` builds `included` from the attacker-influenced preprocess map [6](#0-5) .

### Impact Explanation
An unprivileged party who can cause a node to call `ThresholdKeys::read` on chosen bytes (the deserialization path is explicitly part of the untrusted-input surface) can burn `O(n²)` CPU — billions of field operations per message — starving the node's other work, exactly the DoS class of the reference advisory. For `Interpolation::Constant`, `t` must equal `n` [7](#0-6) , so the worst case needs `n` scalars plus `n` points in the payload, still only ~4 MB for minutes-scale CPU on a single core.

### Likelihood Explanation
Any service that deserializes `ThresholdKeys` (or Lagrange-interpolated views over attacker-influenced signing sets) from untrusted input is exposed. No authentication, threshold control, or key knowledge is needed — the cost is paid during parameter validation/deserialization itself, before any secret-dependent operation.

### Recommendation
- Cap `t`/`n` at a sane protocol maximum (e.g. the actual validator-set size) inside `ThresholdParams::new` or `ThresholdKeys::read` before any interpolation work.
- Precompute Lagrange denominators once per signing set (batch-invert, or compute `interpolation_factor` over the full set in `O(k)` total via the standard prefix/suffix-product trick) instead of `O(k)` per participant.
- Reject `Interpolation::Constant` payloads where `t != n` before reading the `n` coefficient scalars.

### Proof of Concept
```rust
// Attacker crafts a ThresholdKeys<Ristretto> payload with t = n = 65535, Lagrange.
let n: u16 = u16::MAX;
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(n.to_le_bytes());          // t = 65535
buf.extend(n.to_le_bytes());          // n = 65535
buf.extend(1u16.to_le_bytes());       // i = 1
buf.push(1);                          // Interpolation::Lagrange
buf.extend(Scalar::ONE.to_repr());    // secret_share
for _ in 0 .. n {
  buf.extend(Ristretto::generator().to_bytes()); // verification_shares
}
// ThresholdKeys::new evaluates interpolation_factor for i in 1..=t,
// each O(t) with an inversion: ~4.3e9 scalar muls + 65535 inversions
// from a ~2.1 MB input.
let _ = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
```

### Citations

**File:** crypto/dkg/src/lib.rs (L229-247)
```rust
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

**File:** crypto/dkg/src/lib.rs (L368-372)
```rust
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
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

**File:** crypto/dkg/src/lib.rs (L620-632)
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
  }
```

**File:** crypto/frost/src/sign.rs (L290-312)
```rust
    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
```
