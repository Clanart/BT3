### Title
Quadratic-complexity denial of service in `ThresholdKeys::read`/`new`/`view` via attacker-controlled participant count - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts a fully attacker-controlled `n` (a `u16` from the byte stream) and reads `n` verification shares, then `ThresholdKeys::new` and `ThresholdKeys::view` perform Lagrange interpolation whose cost is O(n²) field operations plus O(n) field inversions. A ~4 MB input claiming `n = 65535` forces billions of field multiplications and tens of thousands of field inversions, stalling the caller — the direct analog of a ReDoS/superlinear-work DoS on untrusted bytes.

### Finding Description
In `crypto/dkg/src/lib.rs`:

- `ThresholdKeys::read` reads `t`, `n`, `i` directly from the reader. For `Interpolation::Constant` it does `Vec::with_capacity(usize::from(n))` and `n` scalar reads, and unconditionally reads `n` group elements into `verification_shares` (lines 604–623). `n` is bounded only by `u16::MAX` (65535), and the only validation is `ThresholdParams::new` requiring `t <= n` — so an input with `t = n = 65535` is fully "valid".

- `ThresholdKeys::new` computes the group key as `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` (lines 376–378). For Lagrange interpolation, `interpolation_factor` loops over the entire `included` set performing `num *= share; denom *= share - i_f` and a field `invert()` per call (lines 234–246). That is O(t) multiplications and one inversion per participant, i.e. O(t²) multiplications and O(t) inversions total.

- `ThresholdKeys::view` repeats the same pattern for every participant in `included` (which may be up to `n`), calling `interpolation_factor` per signer at line 505 — again O(n²) plus O(n) inversions, and additionally O(n) group multiplications.

For `t = n = 65535`, a single `read`+`new` performs ~4.3×10⁹ field multiplications and 65535 field inversions; a subsequent `view` with a full signing set repeats this. Each `denom.invert().unwrap()` is a modular exponentiation — far more expensive than a multiplication — so the dominant constant factor is large. Input-to-work amplification is quadratic, matching the foreman ReDoS class (a small crafted input forcing disproportionate computation).

### Impact Explanation
Any code path that calls `ThresholdKeys::read` (listed among the permitted untrusted-input entry points), `ThresholdKeys::new`, or `ThresholdKeys::view` on adversarially supplied parameters can be stalled for an extended period on a single message. If reached during DKG key reconstruction, multisig setup, or signing-set view computation, an unprivileged party can halt or severely delay threshold signing — an availability failure of the signing pipeline (CWE-400, analogous to the referenced advisory's impact).

### Likelihood Explanation
Reachability depends on whether untrusted bytes ever reach `ThresholdKeys::read`/`new`/`view` with attacker-chosen `n`. The parameters `t`/`n` are protocol-level values, so this requires a path where the serialized keys or DKG outputs are attacker-influenced rather than locally fixed; in coordinator/processor usage `n` is typically the validator-set size (small, consensus-bounded), which would bound the cost in practice. Exploitation is therefore plausible but configuration-dependent — consistent with Medium severity.

### Recommendation
Cap `n` to a protocol-appropriate maximum (e.g. the real `MAX_KEY_SHARES_PER_SET` bound used elsewhere in the codebase, ~150) inside `ThresholdKeys::read` before allocating or reading shares, and reject `n` values that exceed the expected set size in `ThresholdKeys::new`/`view`. If large sets are genuinely needed, compute the group key via batch inversion (invert all denominators in one pass with Montgomery's trick) to reduce the O(n) inversion cost to O(n) multiplications.

### Proof of Concept
```rust
// Conceptual: serialize a ThresholdKeys blob with n = u16::MAX.
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(&u16::MAX.to_le_bytes()); // t = 65535
buf.extend(&u16::MAX.to_le_bytes()); // n = 65535
buf.extend(&1u16.to_le_bytes());     // i = 1
buf.push(1);                         // Lagrange interpolation
buf.extend(F::ONE.to_repr());        // secret_share
// Append 65535 valid encoded group elements (~2 MB for 32-byte encodings)
for _ in 0 .. u16::MAX { buf.extend(G::generator().to_bytes()); }

let keys = ThresholdKeys::<C>::read(&mut &buf[..]).unwrap();
// ~4.3e9 field muls + 65535 inversions just to construct keys.
// keys.view((1..=u16::MAX).map(Participant).collect()) repeats the quadratic work.
```
Confirmed code locations: [1](#0-0)  (read of attacker-controlled `n`), [2](#0-1)  (O(n) `interpolation_factor` with per-call inversion), [3](#0-2)  (O(t²) group-key computation in `new`), [4](#0-3)  (O(n²) loop in `view`).

Note: I was unable to fully verify whether production coordinator/processor code feeds remote-controlled bytes into `ThresholdKeys::read` with attacker-set `n` before hitting a consensus-bounded size check; if `n` is always derived from the fixed validator set, the practical exposure is reduced, but the library-level quadratic DoS on untrusted input remains.

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
