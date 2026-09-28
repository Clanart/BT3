### Title
Untrusted `ThresholdKeys` serialization triggers quadratic Lagrange interpolation and hangs the caller - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts a 16-bit participant count and threshold, deserializes that many public verification shares, then calls `ThresholdKeys::new`. For Lagrange-interpolated keys, `new` derives the group key by calculating an interpolation factor for every participant `1..=t`; each factor calculation iterates over all `t` participants. A maximum-size serialized key therefore forces roughly `65535²` field operations, creating a repeatable CPU denial of service from attacker-controlled bytes.

### Finding Description
`ThresholdKeys::read` reads attacker-controlled `t`, `n`, and `i` values, permits `n` up to `65535`, and then reads one verification share for each participant before invoking `ThresholdKeys::new`. [1](#0-0) 

`ThresholdKeys::new` only checks the number and indexes of the supplied shares before computing `group_key` as the sum of `verification_shares[i] * interpolation_factor(i, 1..=t)` for every participant in the threshold range. [2](#0-1) 

For `Interpolation::Lagrange`, each `interpolation_factor` call iterates over the complete `included` list and performs field multiplication and inversion work proportional to `t`. [3](#0-2) 

Consequently, a payload declaring `t = n = 65535` causes approximately 4.29 billion inner-loop iterations during deserialization. The verification shares do not need to correspond to a meaningful distributed key; canonical encodings of arbitrary points are enough to reach the expensive calculation.

### Impact Explanation
An unprivileged party that can supply serialized `ThresholdKeys` bytes to a verification, signing, import, recovery, or deserialization path can cause that thread to remain in `ThresholdKeys::new` for an extreme amount of CPU time. Repeated submissions can exhaust available workers or make a signer/verifier unresponsive.

This maps the reported vulnerability class—remotely supplied input causing a hang or repeatable crash—onto Serai's deserialization path. The payload is approximately a few MiB because each claimed participant requires one encoded group element, while the resulting work is quadratic rather than proportional to input size.

### Likelihood Explanation
The issue is reachable whenever an application deserializes `ThresholdKeys` from storage, a peer, a submitted proof payload, recovery data, or another trust boundary using the public `ThresholdKeys::read` API. No private key, validator privileges, collusion, malformed curve implementation, or protocol violation beyond providing crafted bytes is required.

The attack requires sending enough encoded shares to pass the `n`-count deserialization loop, but that cost remains small relative to the resulting billions of field operations. Since the expensive operation occurs automatically inside `read`, callers that return `io::Result` do not have an opportunity to impose a protocol-sized participant bound before the quadratic work begins.

### Recommendation
Before interpolating in `ThresholdKeys::read` or `ThresholdKeys::new`, enforce a documented maximum threshold/participant count appropriate for production deployments. Alternatively:

- reject `n` values above a conservative limit immediately after reading `t`, `n`, and `i`;
- derive `group_key` with a linear-time construction where possible;
- defer expensive interpolation until semantic validation succeeds; and
- ensure public deserialization APIs impose bounded work independent of the serialized `u16` maximum.

The limit should be enforced inside `ThresholdKeys::read`/`ThresholdKeys::new`, not left to callers, because the panic/hang exposure is otherwise easy to miss.

### Proof of Concept
Conceptually, construct a serialized `ThresholdKeys<Ristretto>` or another supported `Ciphersuite` as follows:

```text
id_len              = little-endian u32 length of C::ID
id                  = C::ID
t                   = 0xffff little-endian
n                   = 0xffff little-endian
i                   = 0x0001 little-endian
interpolation       = 0x01                  // Lagrange
secret_share        = canonical encoding of scalar 1
verification_shares = 65,535 repetitions of C::generator().to_bytes()
```

Then pass the byte slice to:

```rust
ThresholdKeys::<C>::read(&mut bytes.as_slice())
```

`ThresholdParams::new` accepts `t = n = i` within range, and `Interpolation::Lagrange` avoids reading the constant-coefficient vector. Deserialization still reads all 65,535 points and then invokes `ThresholdKeys::new`, which executes 65,535 interpolation-factor calculations. Each calculation scans the complete `1..=65535` participant list, yielding approximately `65,535²` loop iterations and a sustained CPU hang.

### Citations

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

**File:** crypto/dkg/src/lib.rs (L355-378)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L591-631)
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
```
