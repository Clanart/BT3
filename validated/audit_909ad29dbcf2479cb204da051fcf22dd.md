### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` enables a CPU-exhaustion DoS via `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts a fully attacker-controlled `n`/`t` (both unbounded `u16` up to 65535) and then calls `ThresholdKeys::new`, which computes the group key via per-participant Lagrange interpolation. `interpolation_factor` is O(len(included)) per participant and is invoked for every participant `1..=t`, giving O(t·t) field multiplications plus `t` field inversions from a single serialized blob. This is the same bug class as CVE-2018-15607: a small structured input causing disproportionate CPU consumption (hang) in a deserialization path.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw `u16`s with no upper bound beyond `t <= n <= 65535` enforced later in `ThresholdParams::new` [1](#0-0) . It then reads `n` verification shares and calls `ThresholdKeys::new` [2](#0-1) . `ThresholdKeys::new` computes `group_key` by summing `verification_shares[i] * interpolation_factor(i, t)` for `i in 1..=t` [3](#0-2) . Under `Interpolation::Lagrange`, each `interpolation_factor` call iterates the full `included` set, performing ~2 field multiplications per element plus one `F::invert()` (an inversion costing on the order of hundreds of multiplications) [4](#0-3) . With `t = n = 65535`, a single deserialization performs ~4.3×10⁹ field multiplications and 65535 inversions — minutes of single-threaded CPU — before the call returns or errors.

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (or trigger `ThresholdKeys::new`/`view` with an attacker-influenced `included` set / large `n` through DKG parameters) causes a sustained CPU hang during deserialization, stalling the affected validator/processor thread. This matches the accepted impact class: resource exhaustion reachable from untrusted input to an in-scope `read` API.

### Likelihood Explanation
Reachability depends on integrators deserializing `ThresholdKeys` from sources influenced by remote input; the cryptographic amplification itself is unconditional — no malformed encoding is needed, only `t = n` near `u16::MAX` and `n` valid point encodings (≈2 MB, for orders-of-magnitude CPU amplification). Note the input is not "tiny" like the ImageMagick PoC: the reader must consume `n` point encodings before `ThresholdKeys::new` runs, so this is a moderate amplification DoS rather than a byte-level pathological case.

### Recommendation
- Bound `n`/`t` to the protocol's actual maximum validator set size before interpolation (e.g., reject `n` above a few hundred in `ThresholdKeys::read`/`ThresholdParams::new`).
- Cache Lagrange numerators/denominators: compute the group key as a single O(t) multi-evaluation (prefix products + one batch inversion via Montgomery's trick) instead of O(t) independent `interpolation_factor` calls each doing its own `invert()`.
- Apply the same memoization in `ThresholdView::view`, which calls `interpolation_factor` per included signer [5](#0-4) .

### Proof of Concept
```rust
// Pseudocode against crypto/dkg (any Ciphersuite C with 32-byte points/scalars)
let mut buf = vec![];
buf.extend(4u32.to_le_bytes());          // C::ID len (assume 4)
buf.extend(b"Rist");                     // C::ID
buf.extend(0xFFFFu16.to_le_bytes());     // t = 65535
buf.extend(0xFFFFu16.to_le_bytes());     // n = 65535
buf.extend(1u16.to_le_bytes());          // i = 1
buf.push(1);                             // Interpolation::Lagrange
buf.extend([0u8; 32]);                   // secret_share encoding
for _ in 0 .. 0xFFFF { buf.extend(valid_point_bytes); } // n verification shares
// ThresholdKeys::<C>::read(&mut &buf[..]) -> runs ~4.3e9 field muls + 65535 inversions
```
Caveat I could not fully verify without further tracing: whether a production call site pipes remote-controlled bytes into `ThresholdKeys::read` (the coordinator/processor deserialization sites were outside the permitted scope to confirm here). The quadratic complexity itself is confirmed directly in `crypto/dkg/src/lib.rs`.

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
