### Title
Attacker-controlled `n`/`t` in `ThresholdKeys::read` causes quadratic deserialization and group-key computation, stalling signing - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The external report's bug class is a loop whose bound grows with attacker-influenced state, turning a routine operation into work that can no longer complete. Serai's analog lives in `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg/src/lib.rs`: the participant count `n` and threshold `t` are read directly from untrusted bytes with only the u16 ceiling (65535), and `ThresholdKeys::new` then computes the group key by evaluating a Lagrange interpolation factor per participant — an `O(t²)` loop that reaches ~4.3×10⁹ field multiplications from a few megabytes of input.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw u16s, reads `n` scalars for `Interpolation::Constant` or a tag for `Lagrange`, then loops `1..=n` calling `C::read_G` to populate `verification_shares`, before calling `ThresholdKeys::new` ( [1](#0-0) ). Inside `new`, the group key is computed as `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` ( [2](#0-1) ). For `Interpolation::Lagrange`, `interpolation_factor` iterates over the entire `included`/`t` slice per call ( [3](#0-2) ), yielding `O(t²)` scalar multiplications and an inversion each. The same pattern repeats in `view()`, which calls `interpolation_factor` for every included participant and adds `get_mut`/generator work on top ( [4](#0-3) ). Like the Fiinu `allFNUHolders` loop, the work performed is proportional to a state value (`n`, `t`) that is not constrained by anything other than the u16 type — the caller has no way to cap it, and no bound is checked against an actual deployment's validator set size.

### Impact Explanation
`ThresholdKeys::read` is on the explicitly permitted untrusted-input surface. Supplying serialized keys with `n = t = 65535` forces ~65k point decodings plus ~4.3×10⁹ field multiplications and 65k inversions inside `new`, and again inside any subsequent `view()` call. This monopolizes the deserializing processor's CPU for an effectively unbounded duration, halting DKG/signing progress for that validator. Since Serai signing requires a live threshold of validators, stalling honest nodes delays or prevents batch signatures — outputs the validator set has received become temporarily unspendable, which is the liveness analogue of Fiinu's "impossible to make a `transfer` call".

### Likelihood Explanation
The trigger requires only that attacker-influenced bytes reach `ThresholdKeys::read` — no collusion, no valid share, no special position. The cost asymmetry is severe: the attacker supplies on the order of `33 + n·(point_bytes)` bytes (~2–4 MB worst case) to induce billions of field operations. The main caveat is reachability: whether a practical deployment actually deserializes `ThresholdKeys` from a party the victim doesn't already trust determines whether this is a remote trigger or a hardening issue; within the rules of this review it is treated as reachable. Severity: Medium (liveness degradation, no direct secret/key compromise).

### Recommendation
Cap `t`/`n` at the maximum validator-set size the deployment supports (e.g., a `MAX_PARTICIPANTS` constant well below u16::MAX) inside `ThresholdParams::new` or at the top of `ThresholdKeys::read`, before any allocation or interpolation. Additionally, compute the group key with a multiexp-friendly linear pass (interpolation factors can be derived in `O(t log t)` or via prefix/suffix products in `O(t)`), and avoid the per-participant `invert()` by batch-inverting denominators.

### Proof of Concept
```rust
// crypto/dkg — craft serialized ThresholdKeys with maximal n/t
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend(C::F::ONE.to_repr().as_ref()); // secret_share
for _ in 0..65535 { buf.extend(C::generator().to_bytes().as_ref()); }
// Each victim call performs ~4.3e9 scalar muls + 65k inversions + 65k decompressions
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
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

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L500-521)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```

**File:** crypto/dkg/src/lib.rs (L591-632)
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
  }
```
