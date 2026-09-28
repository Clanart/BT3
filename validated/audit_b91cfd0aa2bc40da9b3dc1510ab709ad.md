### Title
Cached FROST preprocesses are not bound to the key or algorithm used to resume them - (File: crypto/frost/src/sign.rs)

### Summary
`CachedPreprocess` is only a 32-byte RNG seed. `AlgorithmSignMachine::from_cache` accepts arbitrary `ThresholdKeys` and algorithm state alongside that seed, then deterministically regenerates the nonce material without verifying that either matches the machine which produced the cache. The nonce derivation uses the stored seed and `original_secret_share`, but not the resumed key’s participant index, verification shares, group parameters, scalar, or offset. Consequently, a cached preprocess generated under one key/algorithm context can be resumed under a different context and reused to sign public inputs rather than being rejected.

### Finding Description
`CachedPreprocess` contains only `Zeroizing<[u8; 32]>` and carries no identity of the `ThresholdKeys`, algorithm, transcript, participant index, group key, scalar, offset, or preprocess it represents. [1](#0-0)  `from_cache` forwards whichever `algorithm` and `keys` are supplied to `seeded_preprocess` without checking them against the cache. [2](#0-1) 

`seeded_preprocess` initializes `ChaCha20Rng` directly from the cached seed and derives commitments and addenda under the newly supplied parameters. [3](#0-2)  Nonce generation draws deterministic seed bytes from that RNG and combines them only with `original_secret_share`, which intentionally excludes ephemeral scalar and offset state. [4](#0-3) [5](#0-4) [6](#0-5) 

This is the same failure shape as session resumption without SNI/ALPN binding: resumption succeeds under a different security context because the cached state is not bound to the parameters under which it is resumed.

### Impact Explanation
The cached seed deterministically defines the underlying `d` and `e` nonce pair whenever it is resumed with the same original secret share. Reusing it across different key or algorithm contexts can therefore produce repeated nonce relationships in otherwise distinct signing sessions. Once an unprivileged counterparty obtains enough signature shares produced from the same cached seed under attacker-influenced messages or contexts, the linear equations can be solved for the signer’s secret share or used to induce shares inconsistent with the intended signing context.

The resulting share is a valid Schnorr response of the form `s = r + c * x`, so nonce reuse across differing challenges or binding factors exposes the private contribution through standard Schnorr nonce-reuse algebra. [7](#0-6) 

### Likelihood Explanation
This is a Medium-severity API flaw rather than an unconditional protocol failure. Exploitation requires a signer to persist a `CachedPreprocess` and later resume it with different `ThresholdKeys` or algorithm state, which is a realistic restart/resumption failure mode because the API accepts both values independently and performs no binding check. The vulnerable path is reachable through the public `SignMachine::from_cache` API and subsequent `sign` call with attacker-supplied preprocesses and message bytes. [8](#0-7) 

### Recommendation
Bind every cached preprocess to its full resumption context. At minimum, derive or store an authentication tag over:

- the ciphersuite identifier;
- `t`, `n`, and local participant index;
- canonical `group_key`;
- all verification shares or a commitment to them;
- interpolation variant and coefficients;
- current scalar and offset;
- algorithm/context identifier;
- generated public preprocess.

`from_cache` should return an error unless the supplied `algorithm` and `ThresholdKeys` reproduce that binding. A safer design is to derive the actual nonce seed as `KDF(cache_seed, context_commitment)` rather than using `cache_seed` directly.

### Proof of Concept
The vulnerable sequence is directly exposed by the API:

```rust
let (machine_a, _public_preprocess) =
    AlgorithmMachine::new(algorithm_a.clone(), keys_a.clone()).preprocess(&mut rng);

let cache = machine_a.cache();

// Resumption succeeds even though keys_b and/or algorithm_b differ from the
// context that generated `cache`.
let (machine_b, _reused_preprocess) =
    AlgorithmSignMachine::from_cache(algorithm_b, keys_b, cache);

let (_signature_machine_b, share_b) =
    machine_b.sign(attacker_supplied_preprocesses, attacker_supplied_msg)?;
```

`from_cache` has no rejected-mismatch path and simply reconstructs the deterministic preprocess with `ChaCha20Rng::from_seed(*seed.0)`. [2](#0-1) [9](#0-8)  Repeating the same cached seed under multiple accepted contexts and collecting the emitted `SignatureShare` values supplies the equations needed for nonce-reuse recovery of the secret share.

### Citations

**File:** crypto/frost/src/sign.rs (L83-92)
```rust
/// A cached preprocess.
///
/// A preprocess MUST only be used once. Reuse will enable third-party recovery of your private
/// key share. Additionally, this MUST be handled with the same security as your private key share,
/// as knowledge of it also enables recovery.
// Directly exposes the [u8; 32] member to void needing to route through std::io interfaces.
// Still uses Zeroizing internally so when users grab it, they have a higher likelihood of
// appreciating how to handle it and don't immediately start copying it just by grabbing it.
#[derive(Zeroize)]
pub struct CachedPreprocess(pub Zeroizing<[u8; 32]>);
```

**File:** crypto/frost/src/sign.rs (L121-141)
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
```

**File:** crypto/frost/src/sign.rs (L216-240)
```rust
  /// Create a sign machine from a cached preprocess.
  ///
  /// After this, the preprocess must be deleted so it's never reused. Any reuse will presumably
  /// cause the signer to leak their secret share.
  fn from_cache(
    params: Self::Params,
    keys: Self::Keys,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess);

  /// Read a Preprocess message.
  ///
  /// Despite taking self, this does not save the preprocess. It must be externally cached and
  /// passed into sign.
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess>;

  /// Sign a message.
  ///
  /// Takes in the participants' preprocess messages. Returns the signature share to be broadcast
  /// to all participants, over an authenticated channel. The parties who participate here will
  /// become the signing set for this session.
  fn sign(
    self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
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

**File:** crypto/frost/src/nonce.rs (L107-123)
```rust
  pub(crate) fn new<R: RngCore + CryptoRng>(
    rng: &mut R,
    secret_share: &Zeroizing<C::F>,
    planned_nonces: &[Vec<C::G>],
  ) -> (Vec<Nonce<C>>, Commitments<C>) {
    let mut nonces = vec![];
    let mut commitments = vec![];

    for generators in planned_nonces {
      let (nonce, these_commitments): (Nonce<C>, _) =
        NonceCommitments::new(&mut *rng, secret_share, generators);

      nonces.push(nonce);
      commitments.push(these_commitments);
    }

    (nonces, Commitments { nonces: commitments })
```

**File:** crypto/frost/src/curve/mod.rs (L93-120)
```rust
  /// Securely generate a random nonce. H3 from the IETF draft.
  fn random_nonce<R: RngCore + CryptoRng>(
    secret: &Zeroizing<Self::F>,
    rng: &mut R,
  ) -> Zeroizing<Self::F> {
    let mut seed = Zeroizing::new(vec![0; 32]);
    rng.fill_bytes(seed.as_mut());

    let mut repr = secret.to_repr();

    // Perform rejection sampling until we reach a non-zero nonce
    // While the IETF spec doesn't explicitly require this, generating a zero nonce will produce
    // commitments which will be rejected for being zero (and if they were used, leak the secret
    // share)
    // Rejection sampling here will prevent an honest participant from ever generating 'malicious'
    // values and ensure safety
    let mut res;
    while {
      seed.extend(repr.as_ref());
      res = Zeroizing::new(<Self as Curve>::hash_to_F(b"nonce", seed.deref()));
      res.ct_eq(&Self::F::ZERO).into()
    } {
      seed = Zeroizing::new(vec![0; 32]);
      rng.fill_bytes(&mut seed);
    }
    repr.as_mut().zeroize();

    res
```

**File:** crypto/dkg/src/lib.rs (L449-452)
```rust
  /// Return the underlying secret share for these keys, without any tweaks applied.
  pub fn original_secret_share(&self) -> &Zeroizing<C::F> {
    &self.core.secret_share
  }
```

**File:** crypto/schnorr/src/lib.rs (L68-83)
```rust
  /// Sign a Schnorr signature with the given nonce for the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[allow(clippy::needless_pass_by_value)] // Prevents further-use of this single-use value
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
