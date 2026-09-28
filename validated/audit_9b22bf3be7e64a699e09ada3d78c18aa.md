### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read` enables CPU-exhaustion DoS from small untrusted input - (File: crypto/dkg/src/lib.rs)

### Summary

`ThresholdKeys::<C>::read` accepts a fully attacker-controlled `n`/`t` (u16 fields), then calls `ThresholdKeys::new`, which computes the group key by evaluating `interpolation_factor` once per participant in `1..=t`. Each `interpolation_factor` call for `Interpolation::Lagrange` is itself O(t) field multiplications plus one inversion, so deserialization is O(t²) — up to ~4.3×10⁹ field multiplications and 65 535 inversions for `t = n = 65535` — triggered by ~2 MB of input bytes. This mirrors CVE-2020-8416's class: an unprivileged party causes disproportionate resource consumption with public input only.

### Finding Description

The read path:

- `ThresholdKeys::read` reads `t`, `n`, `i` as raw u16s, then a one-byte interpolation selector. Selecting `1` (`Interpolation::Lagrange`) means no per-participant coefficients are read, so the input stays minimal. [1](#0-0) 
- It then reads `n` verification-share points and calls `ThresholdKeys::new(ThresholdParams::new(t, n, i), ...)`. [2](#0-1) 
- `ThresholdKeys::new` builds `t = participants 1..=t` and maps each `i` to `verification_shares[i] * interpolation.interpolation_factor(*i, &t)`. [3](#0-2) 
- For Lagrange, `interpolation_factor` iterates over the entire `included` set (size t) doing one field multiply into `num` and `denom` each, then a scalar inversion — so group-key derivation costs O(t²) field multiplications and t inversions. [4](#0-3) 

The input required is only `6 + 1 + 32 + 33·n` bytes (≈2.1 MB for n=65535), while the CPU work is ~4.3 billion field multiplications plus 65 535 inversions — an amplification factor of roughly three orders of magnitude in work-per-byte.

The same quadratic blowup exists in `ThresholdKeys::view`, which computes `interpolation_factor` for every signer in `included` (each O(|included|)), reachable whenever a corrupted/hostile view request can specify a large included set, though the primary reachable vector is deserialization via `ThresholdKeys::read`. [5](#0-4) 

Note that `ThresholdKeys::new` bounds the constant-interpolation path (`t == n` required) but places no cost cap on Lagrange. [6](#0-5) 

### Impact Explanation

`ThresholdKeys::read` is listed as an untrusted-input sink. Any component that deserializes a `ThresholdKeys` blob supplied by an external party (key-exchange material, recovery data, coordinator-supplied parameters) can be driven into billions of scalar operations per invocation, blocking the calling thread. Repeated submissions keep the node/coordinator saturated — a remote denial of service analogous to the BearFTP connection-flood DoS: no secret leakage, but sustained unavailability of the signing/verification pipeline. At minimum severity Medium (bounded by u16, single-call exhaustion is finite but large); if the read result is required before further protocol progress (key recovery, DKG completion), it can halt an entire signing session.

### Likelihood Explanation

Reachability requires a caller feeding attacker-controlled bytes into `ThresholdKeys::read` — a plausible path for any service accepting serialized key material (recovery flows, reshare data). The attacker needs no valid key share, signature, or protocol role; `n = t = 65535` and `65535` canonical points (e.g., the generator repeated, or all identity encodings rejected? — identity points fail `read_G`'s canonical check only if non-canonical, so use repeated valid encodings) suffice. No cryptographic validity is needed since the quadratic work happens during `ThresholdKeys::new`, before any share-correctness check.

### Recommendation

- Cap `t`/`n` at a protocol-realistic maximum (e.g., `MAX_KEY_SHARES_PER_SET`) in `ThresholdKeys::read` before allocating/verifying, rather than accepting the full u16 range.
- Compute group keys via a single multiexp over precomputed Lagrange coefficients (compute each coefficient once with an O(t) batch-inversion via Montgomery's trick), turning O(t²) muls + t inversions into O(t) muls + 1 inversion.
- If large sets must be supported, defer/background the group-key derivation or verify incrementally.

### Proof of Concept

```rust
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto;
use frost::{Participant, ThresholdKeys};

// Craft a serialized ThresholdKeys<Ristretto> with t = n = 65535, Lagrange interpolation
fn malicious_blob() -> Vec<u8> {
    let n: u16 = u16::MAX;
    let mut buf = vec![];
    // curve ID
    buf.extend((Ristretto::ID.len() as u32).to_le_bytes());
    buf.extend(Ristretto::ID);
    // t, n, i
    buf.extend(n.to_le_bytes());
    buf.extend(n.to_le_bytes());
    buf.extend(Participant::new(1).unwrap().to_bytes());
    // Interpolation::Lagrange
    buf.push(1);
    // secret share
    buf.extend(Ristretto::generator().to_bytes().as_ref()); // any canonical F repr; use 1
    // n verification shares: repeated generator encodings are canonical points
    for _ in 0 .. n {
        buf.extend(Ristretto::generator().to_bytes().as_ref());
    }
    buf
}

// ThresholdKeys::<Ristretto>::read(&mut &blob[..])
//   -> ThresholdKeys::new -> 65535 x interpolation_factor, each iterating 65535 elements
//      plus a field inversion  => ~4.3e9 field mults + 65535 inversions for ~2.1 MB input
```

All paths verified against `crypto/dkg/src/lib.rs` (`read` at L574–632, `new` at L348–391, `interpolation_factor` at L226–249).

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

**File:** crypto/dkg/src/lib.rs (L367-374)
```rust
    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
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

**File:** crypto/dkg/src/lib.rs (L591-616)
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
