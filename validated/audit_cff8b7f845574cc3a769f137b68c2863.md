### Title
ThresholdKeys deserialization permits parameter downgrade to attacker-controlled solo key - ([File: crypto/dkg/src/lib.rs](https://github.com/serai-dex/serai))

### Summary
`ThresholdKeys::read` accepts a fully attacker-supplied `t`, `n`, interpolation coefficients, secret share, and verification shares, then computes `group_key` deterministically from participants `1..=t`. An attacker can serialize a "threshold" key with `t = 1` (or with `Interpolation::Constant` coefficients of their choosing) so the resulting `group_key` is just their own public key. Any consumer that feeds untrusted bytes to `ThresholdKeys::read` and then treats `group_key()` as a genuine threshold output — e.g., to derive a deposit address or set a validator-set key — is silently downgraded from a `t`-of-`n` multisig to a 1-of-1 key the attacker fully controls. This is the structural analog of CVE-2015-2319/FREAK: instead of negotiating a weak EXPORT_RSA cipher, the attacker negotiates a weak (degenerate) threshold parameter set through an unauthenticated serialized format, and the verifier accepts it.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) parses `t`, `n`, `i`, the interpolation variant, the secret share, and `n` verification shares entirely from the reader, then calls `ThresholdKeys::new` (lib.rs:349-391). `ThresholdKeys::new` checks only:

- `verification_shares.len() == n` and indexes `<= n` (lines 355-365)
- `Constant` interpolation only when `t == n` (lines 367-374)
- `t != 0`, `n != 0`, `t <= n`, `i <= n` via `ThresholdParams::new` (lines 166-179)

The group key is then computed as `sum(verification_shares[i] * interpolation_factor(i, [1..=t]))` (lines 376-378). With `t = 1`, the Lagrange factor over the singleton set `{1}` is `num=1 * denom=1 = 1` (lib.rs:229-247), so `group_key == verification_shares[1]` — a point the attacker sets to `G * x` for a known `x`, with `secret_share = x`. Nothing in the format commits to an expected threshold, participant count, or a DKG transcript; the serialized bytes carry no proof that the parameters correspond to any real ceremony.

With `Interpolation::Constant` (requiring `t == n`), the attacker can additionally pick arbitrary coefficients `c[i]`, making `group_key = sum(c[i] * verification_shares[i])` — still fully computable/controlled by the attacker since every input is self-chosen.

### Impact Explanation
An unprivileged party who can cause `ThresholdKeys::read` to consume crafted bytes (listed in scope as an untrusted-bytes entry point) obtains a structurally valid `ThresholdKeys` whose `group_key()` they alone can sign for. Downstream code paths that deposit funds to, or gate authority on, `group_key()` (e.g., bitcoin-serai wallet address derivation, validator-set key registration) would report/use an address spendable solely by the attacker — matching the "funds reported received that are not spendable [by the intended group]" / forged-key acceptance acceptance criteria. The downgrade is invisible: the object passes all internal consistency checks.

### Likelihood Explanation
Exploitation requires an integrator to deserialize `ThresholdKeys` (or a wrapper carrying it) from attacker-influenced bytes rather than from its own DKG output — e.g., importing multisig configurations, recovery blobs, or peer-supplied key material. Within the Serai coordinator/processor flow, keys are produced internally by PedPoP, so the exposure depends on usage; Medium likelihood.

### Recommendation
- Do not derive authority/deposit addresses from deserialized `ThresholdKeys` without pinning expected `t`, `n`, and the expected `group_key` externally (e.g., store the agreed group key alongside and compare).
- Consider recording and verifying the originating DKG context/session hash inside the serialized format, or rejecting `t == 1`/`Constant` interpolations for callers that require distributed trust.
- At minimum, document that `ThresholdKeys::read` makes no authenticity claims about parameters.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, ThresholdParams, Participant, Interpolation};
use zeroize::Zeroizing;
use dalek_ff_group::{Ristretto, Scalar, EdwardsPoint};

// Attacker picks their secret
let x = Scalar::random(&mut rand_core::OsRng);
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes());          // t = 1  <-- downgraded threshold
buf.extend(1u16.to_le_bytes());          // n = 1
buf.extend(Participant::new(1).unwrap().to_bytes());
buf.push(1);                             // Interpolation::Lagrange
buf.extend(x.to_repr().as_ref());        // secret_share = x
buf.extend((Ristretto::generator() * x).to_bytes().as_ref()); // verification_shares[1]

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
assert_eq!(keys.group_key(), Ristretto::generator() * x);
// group_key is now a "threshold" key that only the attacker can sign for.
```

Caveat: I confirmed the read/derive path but could not verify every in-scope consumer's threat model for key import; impact depends on an integrator trusting deserialized parameters. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crypto/dkg/src/lib.rs (L226-248)
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
    }
```

**File:** crypto/dkg/src/lib.rs (L349-391)
```rust
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
  }
```

**File:** crypto/dkg/src/lib.rs (L574-632)
```rust
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
  }
```
