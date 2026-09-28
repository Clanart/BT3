### Title
Secret FROST nonces are exposed through scalar-dependent point multiplication - ([File: crypto/frost/src/nonce.rs])

### Summary
The FROST preprocessing path multiplies each secret nonce scalar by every configured generator to construct the public nonce commitments. For the curve25519-based ciphersuites, this operation reaches curve25519-dalek's table-backed scalar multiplication through `EdwardsPoint` or `RistrettoPoint`, so a local attacker who can request signatures can use cache/timing observations to recover nonce bits and then derive the threshold secret share from the emitted signature share. [1](#0-0) [2](#0-1) 

### Finding Description
`NonceCommitments::new` derives two secret nonce scalars with `C::random_nonce(secret_share, rng)` and immediately calculates `generator * nonce` for each commitment. [3](#0-2)  These commitments are generated during `AlgorithmMachine::seeded_preprocess`, before the secret nonce is later combined with the public binding factor and consumed by `sign_share`. [4](#0-3) [5](#0-4)  The curve25519 ciphersuites expose table-based `EdwardsBasepointTable` and `RistrettoBasepointTable` implementations as their group implementation, making nonce-dependent table/cache accesses part of preprocessing. [2](#0-1) [6](#0-5) 

The emitted Schnorr share is `s = r + c*x`, where `r` is the secret nonce, `c` is computable from public data, and `x` is the threshold secret share. [7](#0-6) [8](#0-7)  Consequently, recovering enough nonce bits through the scalar-dependent multiplication leaks `x` rather than merely revealing whether an operation was fast or slow. [8](#0-7) 

### Impact Explanation
A party able to trigger a signer’s preprocessing operation and observe shared-cache behavior can recover the secret nonce used for that session. Once the returned `SignatureShare` is observed, the signer’s threshold private share can be calculated as `(s - r) / c`, compromising that participant’s long-term FROST key material. [9](#0-8) [8](#0-7)  For threshold configurations where an attacker can collect the required number of compromised shares, this can lead to unauthorized signatures. [10](#0-9) 

### Likelihood Explanation
The vulnerable operation is on a normal signing path: preprocessing derives secret nonces from the private share and immediately performs secret-scalar generator multiplication. [11](#0-10) [3](#0-2)  Exploitation requires a cache-level side-channel capability against the signing process, so it is not exploitable by network input alone, but signing can be triggered through attacker-selected messages and preprocess inputs. [12](#0-11) [13](#0-12) 

### Recommendation
Perform nonce commitment generation with an explicitly constant-time scalar multiplication implementation whose table lookup uses linear scans or constant-time selects rather than nonce-dependent memory indexing. Avoid exposing the scalar multiplication internals through a generic `Group::mul` implementation when the scalar is secret, and add a dedicated constant-time commitment construction API for nonce generation. [14](#0-13) [6](#0-5)  As a defense-in-depth measure, keep nonce derivation and commitment generation isolated from processes that may observe last-level-cache activity. [15](#0-14) 

### Proof of Concept
1. Arrange for a FROST signer using the Ed25519 or Ristretto ciphersuite to run `preprocess` or `from_cache` on a machine where cache activity can be measured. [15](#0-14) [16](#0-15) 
2. Observe the cache lines touched while `NonceCommitments::new` computes `generator * nonce` for `d` and `e`. [3](#0-2) 
3. Recover the nonce scalar `r = d + rho*e` using the observed table indices, the public commitments, and the publicly derived binding factor `rho`. [17](#0-16) [18](#0-17) 
4. Submit a chosen message for signing and obtain the emitted `SignatureShare(s)`. [13](#0-12) [9](#0-8) 
5. Calculate `c = Hram(R, group_key, msg)` and recover the signer’s private threshold share as `x = (s - r) / c`. [8](#0-7) [19](#0-18)

### Citations

**File:** crypto/frost/src/nonce.rs (L53-69)
```rust
  pub(crate) fn new<R: RngCore + CryptoRng>(
    rng: &mut R,
    secret_share: &Zeroizing<C::F>,
    generators: &[C::G],
  ) -> (Nonce<C>, NonceCommitments<C>) {
    let nonce = Nonce::<C>([
      C::random_nonce(secret_share, &mut *rng),
      C::random_nonce(secret_share, &mut *rng),
    ]);

    let mut commitments = Vec::with_capacity(generators.len());
    for generator in generators {
      commitments.push(GeneratorCommitments([
        *generator * nonce.0[0].deref(),
        *generator * nonce.0[1].deref(),
      ]));
    }
```

**File:** crypto/frost/src/nonce.rs (L161-170)
```rust
  pub(crate) fn calculate_binding_factors<T: Clone + Transcript>(&mut self, transcript: &T) {
    for (l, binding) in &mut self.0 {
      let mut transcript = transcript.clone();
      transcript.append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
      // It *should* be perfectly fine to reuse a binding factor for multiple nonces
      // This generates a binding factor per nonce just to ensure it never comes up as a question
      binding.binding_factors = Some(
        (0 .. binding.commitments.nonces.len())
          .map(|_| C::hash_binding_factor(transcript.challenge(b"rho").as_ref()))
          .collect(),
```

**File:** crypto/dalek-ff-group/src/lib.rs (L24-30)
```rust
use dalek::{
  constants::{self, BASEPOINT_ORDER},
  scalar::Scalar as DScalar,
  edwards::{EdwardsPoint as DEdwardsPoint, EdwardsBasepointTable, CompressedEdwardsY},
  ristretto::{RistrettoPoint as DRistrettoPoint, RistrettoBasepointTable, CompressedRistretto},
};
pub use constants::{ED25519_BASEPOINT_TABLE, RISTRETTO_BASEPOINT_TABLE};
```

**File:** crypto/dalek-ff-group/src/lib.rs (L450-455)
```rust
    impl Mul<Scalar> for &$Table {
      type Output = $Point;
      fn mul(self, b: Scalar) -> $Point {
        $Point(&b.0 * self)
      }
    }
```

**File:** crypto/frost/src/sign.rs (L121-143)
```rust
  fn seeded_preprocess(
    self,
    seed: CachedPreprocess,
  ) -> (AlgorithmSignMachine<C, A>, Preprocess<C, A::Addendum>) {
    let mut params = self.params;

    let mut rng = ChaCha20Rng::from_seed(*seed.0);
    let (nonces, commitments) = Commitments::new::<_>(
      &mut rng,
      params.keys.original_secret_share(),
      &params.algorithm.nonces(),
    );
    let addendum = params.algorithm.preprocess_addendum(&mut rng, &params.keys);

    let preprocess = Preprocess { commitments, addendum };

    // Also obtain entropy to randomly sort the included participants if we need to identify blame
    let mut blame_entropy = [0; 32];
    rng.fill_bytes(&mut blame_entropy);
    (
      AlgorithmSignMachine { params, seed, nonces, preprocess: preprocess.clone(), blame_entropy },
      preprocess,
    )
```

**File:** crypto/frost/src/sign.rs (L232-241)
```rust
  /// Sign a message.
  ///
  /// Takes in the participants' preprocess messages. Returns the signature share to be broadcast
  /// to all participants, over an authenticated channel. The parties who participate here will
  /// become the signing set for this session.
  fn sign(
    self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, Self::SignatureShare), FrostError>;
```

**File:** crypto/frost/src/sign.rs (L268-274)
```rust
  fn from_cache(
    algorithm: A,
    keys: ThresholdKeys<C>,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    AlgorithmMachine::new(algorithm, keys).seeded_preprocess(cache)
  }
```

**File:** crypto/frost/src/sign.rs (L283-313)
```rust
  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
    let multisig_params = self.params.multisig_params();

    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```

**File:** crypto/frost/src/sign.rs (L382-410)
```rust
    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);

    Ok((
      AlgorithmSignatureMachine {
        params: self.params,
        view,
        B,
        Rs,
        share,
        blame_entropy: self.blame_entropy,
      },
      SignatureShare(share),
    ))
```

**File:** crypto/schnorr/src/lib.rs (L74-83)
```rust
  pub fn sign(
    private_key: &Zeroizing<C::F>,
    nonce: Zeroizing<C::F>,
    challenge: C::F,
  ) -> SchnorrSignature<C> {
    SchnorrSignature {
      // Uses deref instead of * as * returns C::F yet deref returns &C::F, preventing a copy
      R: C::generator() * nonce.deref(),
      s: (challenge * private_key.deref()) + nonce.deref(),
    }
```

**File:** crypto/frost/src/algorithm.rs (L201-211)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
  }
```
