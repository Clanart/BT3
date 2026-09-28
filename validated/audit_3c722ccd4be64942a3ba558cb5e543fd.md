### Title
Attacker-controlled `t`/`n` in `ThresholdKeys::read` triggers quadratic Lagrange interpolation during deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the pypdf `ASCIIHexDecode` issue (CWE-407, inefficient algorithmic complexity on attacker-supplied bytes), `ThresholdKeys::read` trusts the serialized `t` and `n` fields and immediately runs `ThresholdKeys::new`, which computes the group key by evaluating `interpolation_factor` for every participant `1..=t`. Under `Interpolation::Lagrange`, each `interpolation_factor` call is O(|included|) field multiplications, so construction is O(t²) — roughly 4.3×10⁹ field multiplications for `t = n = u16::MAX` — from a serialized blob of only ~2 MiB (n field/point elements), and it executes before `ThresholdParams::new` validates the parameters' sanity relative to any expected set.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` directly from the byte stream [1](#0-0) , then reads `n` verification shares [2](#0-1)  and calls `ThresholdKeys::new` [3](#0-2) . Inside `ThresholdKeys::new`, the group key is computed as the sum over `1..=t` of `verification_shares[i] * interpolation_factor(i, t)` [4](#0-3) . For `Interpolation::Lagrange`, `interpolation_factor` loops over all `included` participants performing a field multiplication each iteration [5](#0-4) , making total work O(t²) plus `n` point multiplications. `ThresholdParams::new` (t ≤ n, i ≤ n, non-zero) is only applied at the end, and no bound below `u16::MAX` exists on `t`/`n`.

The same quadratic pattern is reachable during signing via `ThresholdKeys::view`/`interpolation_factor` for large signing sets [6](#0-5) , but those counts are protocol-fixed; `ThresholdKeys::read` is the path where the attacker controls `t`/`n` purely through input bytes.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (listed in-scope reachable surface) can pin a CPU core for a prolonged period — ~4.3 billion sequential field multiplications for `t = n = 65535` — while supplying only ~2 MiB of input. If the reader is used to load keys for a peer-supplied or recovered key-share context in a validator/processor loop, this stalls the signing pipeline and can be repeated per message, degrading liveness of threshold operations (DoS), matching the Medium-severity availability impact of the reference advisory.

### Likelihood Explanation
Reachability depends on `ThresholdKeys::read` being exposed to untrusted bytes (the prompt enumerates it as attacker-reachable). The cost multiplier (~65,536 field ops per byte of the `t`/`n` header fields, and O(n) encoded points amplifying to O(n²) field work) is substantial. It requires only two bytes (`t` high) plus `n·(F+G)` bytes of body — no valid signatures or secrets needed.

### Recommendation
Enforce a protocol-meaningful upper bound on `t`/`n` in `ThresholdKeys::read` before allocating/reading `n` elements and before calling `ThresholdKeys::new` (e.g., reject `n` above the maximum supported validator-set size, and/or accept expected `ThresholdParams` as an argument like `Commitments::read` does [7](#0-6) ). Optionally replace the naive O(t²) Lagrange evaluation with a prefix/suffix-product O(t) computation of the group key.

### Proof of Concept
```rust
// crypto/dkg: craft serialized ThresholdKeys with maximal Lagrange params
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend(scalar_repr);            // secret_share (any canonical F)
for _ in 0 .. 65535 { buf.extend(point_repr); } // verification_shares

// ThresholdKeys::read -> ThresholdKeys::new runs O(t^2) Lagrange factors
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

**File:** crypto/dkg/src/lib.rs (L591-601)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L110-127)
```rust
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
```
