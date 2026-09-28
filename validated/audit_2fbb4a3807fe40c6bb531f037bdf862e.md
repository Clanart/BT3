### Title
Quadratic Lagrange interpolation enables resource exhaustion during `ThresholdKeys` deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t` and `n` values up to `u16::MAX` and then calls `ThresholdKeys::new`. When Lagrange interpolation is selected, `ThresholdKeys::new` computes an interpolation factor for each of the first `t` participants, while each interpolation-factor calculation iterates over all `t` included participants. A syntactically valid serialized key with `t = n = 65,535` therefore triggers approximately 4.3 billion scalar-loop iterations and 65,535 field inversions after deserializing roughly 2–4 MB of points, depending on the ciphersuite. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The deserializer reads `t`, `n`, and `i` as attacker-controlled `u16` fields, followed by an interpolation selector and `n` verification-share points. It performs no practical resource limit beyond the `u16` range before invoking `ThresholdKeys::new`. [4](#0-3) 

For `Interpolation::Lagrange`, `ThresholdKeys::new` constructs participants `1..=t` and calls `interpolation_factor` for every participant. [2](#0-1)  Each call iterates over the complete `included` signing set, performs scalar multiplication and subtraction, and computes a field inversion. [3](#0-2) 

The result is `O(t²)` scalar work plus `O(t)` inversions. The attacker can select the maximum `t = n = 65,535`, causing approximately `65,535² = 4,294,836,225` inner-loop iterations. [1](#0-0) [3](#0-2) 

### Impact Explanation
A consumer that deserializes attacker-supplied `ThresholdKeys` bytes can be forced into orders-of-magnitude more computation than the input size suggests. This can exhaust CPU resources and stall the surrounding key-management, signing, or recovery service while processing one syntactically valid object. [5](#0-4) 

The malicious object can remain semantically valid: all `n` required verification shares can encode the same canonical non-identity generator point, and the secret share can encode a canonical scalar. The expensive work is therefore reached through valid deserialization rather than a malformed-encoding failure. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The required input is only a serialized `ThresholdKeys` object with attacker-selected `t`, `n`, interpolation, scalar, and point fields. All values are public encodings; no private key knowledge, malformed curve point, protocol collusion, or invalid participant index is required. [1](#0-0) 

Exploitation requires the application to call `ThresholdKeys::read` on input influenced by an untrusted party, such as an uploaded key backup, migration blob, recovery artifact, or externally supplied serialized key. If serialized `ThresholdKeys` are only ever locally generated and trusted, this path is not externally reachable. [8](#0-7) 

### Recommendation
Impose a protocol-appropriate maximum `n` and `t` before deserializing the verification-share vector or computing the group key. Also validate `t <= n <= MAX_PARTICIPANTS` immediately after reading the parameter fields, rather than waiting until `ThresholdKeys::new`.

For larger supported groups, precompute reciprocal Lagrange denominators or otherwise avoid the current quadratic per-signer calculation. The relevant computation is the nested work caused by calling `interpolation_factor` once for each of `1..=t` while each call scans the same `t`-element set. [2](#0-1) [3](#0-2) 

### Proof of Concept
The following structure produces the worst-case Secp256k1 input:

```rust
use frost::curve::Secp256k1;
use ciphersuite::{Ciphersuite, group::{Group, GroupEncoding, ff::PrimeField}};
use dkg::ThresholdKeys;

let n = u16::MAX; // 65,535

let mut bytes = Vec::new();
bytes.extend((Secp256k1::ID.len() as u32).to_le_bytes());
bytes.extend(Secp256k1::ID);                 // b"secp256k1"
bytes.extend(n.to_le_bytes());               // t
bytes.extend(n.to_le_bytes());               // n
bytes.extend(n.to_le_bytes());               // i = Participant(65535)
bytes.push(1);                               // Interpolation::Lagrange
bytes.extend(<Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref());

let generator = <Secp256k1 as Ciphersuite>::generator().to_bytes();
for _ in 0 .. n {
  bytes.extend(generator.as_ref());
}

// After parsing the input, ThresholdKeys::new performs ~4.29 billion
// Lagrange inner-loop iterations and 65,535 field inversions.
let _ = ThresholdKeys::<Secp256k1>::read(&mut bytes.as_slice());
```

The layout matches the fields consumed by `ThresholdKeys::read`: ciphersuite ID, `t`, `n`, `i`, interpolation selector, secret scalar, and `n` group encodings. [9](#0-8)

### Citations

**File:** crypto/dkg/src/lib.rs (L229-246)
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
```

**File:** crypto/dkg/src/lib.rs (L376-379)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

```

**File:** crypto/dkg/src/lib.rs (L573-631)
```rust
  /// Read keys from a type satisfying `std::io::Read`.
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
      let different = || io::Error::other("deserializing ThresholdKeys for another curve");

      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
        Err(different())?;
      }
    }

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

**File:** crypto/ciphersuite/src/lib.rs (L71-100)
```rust
  /// Read a canonical scalar from something implementing std::io::Read.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }

  /// Read a canonical point from something implementing std::io::Read.
  ///
  /// The provided implementation is safe so long as `GroupEncoding::to_bytes` always returns a
  /// canonical serialization.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
