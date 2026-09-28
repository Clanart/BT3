### Title
Quadratic Lagrange interpolation enables CPU exhaustion during ThresholdKeys deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t` and `n` values up to `u16::MAX`, and Lagrange interpolation then computes `t` interpolation factors with each factor scanning all `t` participants, producing quadratic field work from a relatively small serialized input. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The deserializer reads `t`, `n`, and `i` directly from the input, selects Lagrange interpolation with tag `1`, then reads `n` verification shares before calling `ThresholdKeys::new`. [1](#0-0)  `ThresholdParams::new` only rejects zero parameters, `t > n`, and `i > n`; it does not impose a practical bound on `t` or `n`. [4](#0-3)  During construction, `group_key` invokes `interpolation_factor` once for every participant in `1..=t`. [3](#0-2)  For Lagrange interpolation, each `interpolation_factor` call iterates over the entire `included` set and performs field multiplications and a field inversion. [5](#0-4)  A serialized key with `t = n = 65,535` therefore causes roughly `t * (t - 1)` numerator and denominator multiplication pairs, in addition to `t` inversions and `t` point multiplications. [5](#0-4) [3](#0-2) 

### Impact Explanation
An unprivileged party who can supply bytes to `ThresholdKeys::read` can force expensive scalar and group arithmetic disproportionate to the input size. [6](#0-5)  With the maximum supported parameters, a serialized object containing approximately `65,535` encoded verification shares triggers billions of field multiplications while deriving `group_key`. [7](#0-6) [3](#0-2)  Repeated submissions can exhaust CPU capacity in any service that deserializes attacker-controlled threshold keys.

### Likelihood Explanation
The required input is only valid canonical field and point encodings, since duplicate generator points are accepted as verification shares before the expensive interpolation occurs. [8](#0-7) [7](#0-6)  The attacker does not need valid DKG shares, a coherent secret, or knowledge of the threshold secret because the quadratic `group_key` computation runs before semantic consistency between `secret_share` and the verification shares would matter. [9](#0-8) 

### Recommendation
Reject serialized `ThresholdKeys` whose `t` or `n` exceeds the deployment's supported threshold, and validate the encoded length before reading attacker-controlled counts. [1](#0-0)  Replace the per-participant recomputation of Lagrange factors with a batch computation using prefix/suffix products and batch inversion, or precompute/store validated interpolation data rather than deriving `group_key` through `O(t²)` work during deserialization. [2](#0-1) [3](#0-2) 

### Proof of Concept
The following serialized input reaches the quadratic path with `t = n = u16::MAX`, Lagrange interpolation, and repeated generator points as verification shares:

```rust
use ciphersuite::{group::{ff::{Field, PrimeField}, Group, GroupEncoding}, Ciphersuite};
use dalek_ff_group::Ristretto;
use dkg::ThresholdKeys;

let n = u16::MAX;
let t = n;

let mut bytes = Vec::new();
bytes.extend_from_slice(&(Ristretto::ID.len() as u32).to_le_bytes());
bytes.extend_from_slice(Ristretto::ID);
bytes.extend_from_slice(&t.to_le_bytes());
bytes.extend_from_slice(&n.to_le_bytes());
bytes.extend_from_slice(&1u16.to_le_bytes());
bytes.push(1); // Interpolation::Lagrange
bytes.extend_from_slice(<Ristretto as Ciphersuite>::F::ONE.to_repr().as_ref());

let generator = Ristretto::generator().to_bytes();
for _ in 1 ..= n {
  bytes.extend_from_slice(generator.as_ref());
}

let _ = ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice());
```

The Lagrange tag is selected at `crypto/dkg/src/lib.rs:614`, the unbounded participant values are read at `crypto/dkg/src/lib.rs:591-601`, the `n` verification shares are consumed at `crypto/dkg/src/lib.rs:620-623`, and the quadratic work is triggered by the `group_key` computation at `crypto/dkg/src/lib.rs:376-378`. [10](#0-9) [3](#0-2)

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

**File:** crypto/dkg/src/lib.rs (L224-247)
```rust
impl<F: Zeroize + PrimeField> Interpolation<F> {
  /// The interpolation factor for this participant, within this signing set.
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

**File:** crypto/dkg/src/lib.rs (L347-390)
```rust
impl<C: Ciphersuite> ThresholdKeys<C> {
  /// Create a new set of ThresholdKeys.
  pub fn new(
    params: ThresholdParams,
    interpolation: Interpolation<C::F>,
    secret_share: Zeroizing<C::F>,
    verification_shares: HashMap<Participant, C::G>,
  ) -> Result<ThresholdKeys<C>, DkgError> {
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
