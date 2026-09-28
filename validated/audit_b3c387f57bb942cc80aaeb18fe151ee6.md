### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` enables CPU denial of service from small serialized keys - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t`, `n`, and an interpolation flag with no upper bound beyond `u16::MAX`. When the `Lagrange` variant is selected, `ThresholdKeys::new` computes `interpolation_factor` (an O(t) loop ending in a field inversion) once per participant in `1..=t`, giving O(t²) field multiplications and O(t) inversions. A serialized blob of a few MB therefore triggers billions of field operations, hanging the caller — the same resource-exhaustion class as the ReDoS advisory, expressed over field arithmetic instead of a regex.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, and `i` as bare `u16`s from the input stream [1](#0-0) . The `Lagrange` interpolation variant requires no additional bytes at all — a single flag byte [2](#0-1) . It then reads `n` verification shares (~32 bytes each for Ristretto/ed25519-class groups) [3](#0-2)  and calls `ThresholdKeys::new`, which only checks `t <= n`, both non-zero — values up to 65535 are accepted [4](#0-3) .

`ThresholdKeys::new` computes the group key as a sum over `t` participants, each term calling `interpolation_factor` [5](#0-4) . For `Lagrange`, `interpolation_factor` iterates the full `included` slice of length `t`, performing ~2 field multiplications per step plus one inversion [6](#0-5) . Total cost: O(t²) multiplications plus O(t) inversions. With `t = n = 65535`, that is ~4.3 billion field multiplications triggered by roughly `n × 33` bytes (~2.2 MB) of input — a ~10⁶× work amplification.

The same quadratic blowup is reachable a second time through `ThresholdView`: `view()` calls `interpolation_factor` once per included signer, each call O(|included|) with an inversion [7](#0-6) , so any caller that feeds deserialized keys into `view()` burns O(n²) again.

### Impact Explanation
Any component that calls `ThresholdKeys::read` on bytes influenced by an unprivileged party (e.g., multisig key material relayed through coordinator tributary transactions, where DKG payloads are signed by arbitrary participants) can be stalled for minutes to hours inside a single deserialization, freezing the signing pipeline. No secret is leaked, but the node's threshold-signing capacity is fully denied — availability loss at High severity per the analog's CWE-400 classification. The `Constant` variant is not immune either: `Vec::with_capacity(n)` plus `n` `read_F` calls is bounded, but `ThresholdKeys::new` still requires `t == n` there, so Lagrange is the amplified path.

### Likelihood Explanation
Reachability is high: `ThresholdKeys::read` is a public deserialization API explicitly fed with externally-originated bytes in this codebase's DKG flow. The trigger is trivially cheap for the attacker — a flag byte `1`, two u16s set to 65535, a participant index, one scalar, and 65535 group elements, none of which need to be valid points for honest signing. Legitimate multisigs in this system have n in the hundreds, so a cap would never reject honest traffic. The only constraint is that the field/group decoding itself (`read_G`, ~n decompressions) already costs O(n), so an attacker paying ~2 MB of bandwidth buys ~seconds-to-minutes of CPU — asymmetric in the attacker's favor but bounded per message.

### Recommendation
Enforce a sane upper bound on `n` and `t` (e.g., the protocol's actual maximum multisig size) inside `ThresholdParams::new` or at the top of `ThresholdKeys::read`, before any allocation or interpolation. Additionally, replace the per-participant O(t) `interpolation_factor` computation in `ThresholdKeys::new` and `ThresholdView::view` with an O(n)-total computation (compute the full numerator product once, or use prefix/suffix products), and batch the inversions via Montgomery batch inversion to remove the per-signer `invert()` calls [8](#0-7) .

### Proof of Concept
```rust
use std::io;
use std_shims::collections::HashMap;
use zeroize::Zeroizing;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Interpolation};

// Serialize a ThresholdKeys blob with Lagrange interpolation, t = n = u16::MAX.
// Structure (from ThresholdKeys::write, crypto/dkg/src/lib.rs:538):
//   id_len || id || t || n || i || 0x01 (Lagrange) || secret_share || n * G
fn malicious_blob<C: Ciphersuite>() -> Vec<u8> {
    let n = u16::MAX;
    let mut buf = vec![];
    buf.extend((C::ID.len() as u32).to_le_bytes());
    buf.extend(C::ID);
    buf.extend(n.to_le_bytes());          // t = 65535
    buf.extend(n.to_le_bytes());          // n = 65535
    buf.extend(1u16.to_le_bytes());       // i = 1
    buf.push(1);                          // Interpolation::Lagrange
    buf.extend(C::F::ZERO.to_repr().as_ref()); // secret_share
    let g = C::generator().to_bytes();
    for _ in 0 .. n {
        buf.extend(g.as_ref());           // verification shares (all identical)
    }
    buf
}

// Victim: ThresholdKeys::<C>::read(&mut blob.as_slice())
//   -> ThresholdKeys::new computes group_key via interpolation_factor over 1..=65535:
//      sum of 65535 terms, each looping 65535 times with field muls + one invert()
//      => ~4.3e9 field multiplications + 65535 inversions => CPU hang
```
Input size ≈ `6 + 2 + 2 + 2 + 1 + 32 + 65535 × 33` ≈ 2.2 MB; work performed ≈ 4.3 billion field multiplications — the deserialization effectively never completes, denying service to any thread that calls `ThresholdKeys::read` on this input.

### Citations

**File:** crypto/dkg/src/lib.rs (L166-178)
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
