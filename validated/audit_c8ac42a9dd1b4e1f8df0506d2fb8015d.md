### Title
Untrusted `n` in `ThresholdKeys::read` triggers quadratic interpolation work and mass point-multiplication during `ThresholdKeys::new` — CPU-exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary
The CVE-2023-43786 class (a local party supplying input that makes a library function burn unbounded CPU) maps onto `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`. Deserialization takes `n` (and `t`) directly from attacker-controlled bytes with no upper bound other than `u16::MAX`, reads `n` verification shares, and then `ThresholdKeys::new` computes the group key by evaluating a Lagrange `interpolation_factor` for every one of the `t = n` participants — an O(t²) field operation loop plus `t` field inversions and `t` scalar-point multiplications. A ~2 MiB input of validly-encoded points therefore forces ~4.3×10⁹ field multiplications, 65,535 field inversions, and 65,535 point multiplications, pinning a CPU core for an extended period.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` as raw u16s from the reader, then loops `for l in (1 ..= n)` calling `C::read_G(reader)` for each verification share [1](#0-0) . `ThresholdParams::new` only enforces `t <= n` and `t, n != 0`, so `t = n = 65535` is accepted [2](#0-1) .

`ThresholdKeys::new` then computes the group key as `sum over i in 1..=t of verification_shares[i] * interpolation_factor(i, t)` [3](#0-2) . For `Interpolation::Lagrange` (tag byte `1`, selectable by the attacker at read time [4](#0-3) ), each `interpolation_factor` iterates the full `included` set doing one field multiply-add per element and ends with a field inversion [5](#0-4)  — so group-key derivation alone is t² field multiplications and t inversions, before the t point scalar-multiplications.

The same quadratic pattern exists in `ThresholdKeys::view`, which computes an interpolation factor per included participant (O(|included|²)) [6](#0-5)  — though that path requires holding keys, whereas `ThresholdKeys::read` is directly reachable with untrusted bytes per the stated threat model.

### Impact Explanation
`ThresholdKeys::read` is explicitly a sink for untrusted bytes. An unprivileged party who can cause a Serai component to deserialize `ThresholdKeys` (e.g., untrusted bytes fed to `read`) can supply `t = n = i = 65535`, interpolation tag `1`, and ~2 MiB of valid canonical point encodings (65535 × 32-byte points plus a scalar). `ThresholdKeys::new` will then perform ~4.3×10⁹ field multiplications, 65,535 inversions, and 65,535 point scalar multiplications — on the order of tens of seconds to minutes of single-core CPU for a ~2 MiB message — a strong CPU/memory amplification denial of service against any process that deserializes these keys from untrusted input, directly analogous to the PutSubImage() resource-consumption flaw.

### Likelihood Explanation
Likelihood depends on a reachable `ThresholdKeys::read` call on attacker-controlled bytes; the rules explicitly list `ThresholdKeys::read` as an untrusted-input entry point, so the path is in scope. No cryptographic trickery is needed — all bytes are well-formed and canonical; the attack is purely parameter inflation. I could not verify whether a production network-facing path actually calls `ThresholdKeys::read` on peer bytes within the indexed scope, so reachability beyond the stated sink list is uncertain — the finding stands on the deserialization routine itself.

### Recommendation
Enforce a sane upper bound on `n`/`t` in `ThresholdKeys::read` (e.g., Serai's `MAX_KEY_SHARES_PER_SET`) before allocating or interpolating, and reject `Interpolation::Constant` vectors inconsistent with `n` early. Additionally, compute the group key without the O(t²) naive Lagrange loop (e.g., batch-invert denominators once and reuse, or accept only Constant interpolation where O(n) suffices for t=n), and bound `view()`'s `included` length to the params' `n` — already done — while capping `n` itself at deserialization.

### Proof of Concept
Conceptual: build a byte buffer for `ThresholdKeys::<Ristretto>::read` containing `C::ID` ("ristretto"), `t = 0xFFFF`, `n = 0xFFFF`, `i = 1`, interpolation tag `1` (Lagrange), one 32-byte canonical scalar, then 65,535 repetitions of a valid canonical Ristretto encoding (e.g., the generator's `to_bytes()`). The buffer is ~2.1 MiB. `ThresholdKeys::read` parses it successfully and `ThresholdKeys::new` executes the quadratic Lagrange interpolation over all 65,535 participants, consuming CPU far disproportionate to the input size — measured by timing `ThresholdKeys::read(&mut buf.as_slice())` with increasing `n` and observing the quadratic scaling.

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
