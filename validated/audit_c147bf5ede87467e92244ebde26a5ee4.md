### Title
Reused cached FROST preprocesses reuse the same secret nonce across distinct signatures - ([File: crypto/frost/src/sign.rs](crypto/frost/src/sign.rs))

### Summary
`AlgorithmSignMachine::from_cache` deterministically reconstructs a signing machine from a reusable `CachedPreprocess` value, but completion does not invalidate that cache or bind it to a specific message/signing set. Reusing the same cached preprocess for two different `sign` calls therefore reuses the same secret nonce while producing publicly transmitted signature shares. [1](#0-0) 

### Finding Description
`AlgorithmSignMachine` stores the cached preprocess seed in `seed: CachedPreprocess`, which is preserved by `cache` and reused by `from_cache` through `seeded_preprocess`. [2](#0-1) 

`sign` consumes the reconstructed in-memory machine, but it has no persistent consumed-state marker and accepts an attacker-influenced `msg` plus a chosen preprocess map for each call. [3](#0-2) 

The function derives the signing set from the supplied preprocesses and only checks quantity, ordering, duplicates, and the upper participant bound; it does not check whether the cached nonce was already used. [4](#0-3) 

The resulting signature share is serialized and returned as public protocol data, so two shares made with the same nonce but different challenges expose the signer’s secret through the standard Schnorr/FROST equation `(share1 - share2) / (challenge1 - challenge2)`, subject to the applicable Lagrange factor. [5](#0-4) 

### Impact Explanation
If the same cached preprocess is used for two messages, or for two different effective signing sets which produce different challenges, an observer can recover the participant’s threshold secret share from the two public shares. That compromise can be combined with other exposed shares to enable unauthorized signatures and directly maps to the missing-state-check class: a cryptographically single-use state remains usable after it should have transitioned to consumed. [6](#0-5) 

### Likelihood Explanation
The cache API explicitly supports reconstructing a signer after restart, making storage and later reuse an expected integration path. Because `from_cache` has no ownership of external durable storage and `sign` cannot mark that storage consumed, parallel retries, duplicated work items, repeated calls after restart, or two signing sets assembled for the same cached preprocess can all trigger reuse. [7](#0-6) 

### Recommendation
Persist a monotonically consumed nonce/cache state and atomically mark a cached preprocess spent before producing a share. Alternatively, derive the secret nonce from `seed || secret_share || msg || normalized_included_set || preprocess_commitments`, so different messages or signing sets cannot reuse the same nonce. The implementation should also reject signing with an already-consumed cache rather than relying on the consumed `self` value alone. [8](#0-7) 

### Proof of Concept
The following reduced PoC shows the vulnerable state transition using the public API:

```rust
// crypto/frost/src/sign.rs

let cache: CachedPreprocess = preprocess_machine.cache();

// First protocol execution: attacker supplies message M1 and preprocess map P1.
let (machine1, our_preprocess1) =
  AlgorithmSignMachine::from_cache(algorithm.clone(), keys.clone(), cache);
let (_signature_machine1, share1) = machine1
  .sign(preprocess_map1, b"message one")
  .unwrap();

// A retry, parallel instance, or second caller restores the same durable cache.
// M2 differs from M1, so the binding factors/challenge differ while the cached
// secret nonce seed is unchanged.
let (machine2, our_preprocess2) =
  AlgorithmSignMachine::from_cache(algorithm, keys, cache);
let (_signature_machine2, share2) = machine2
  .sign(preprocess_map2, b"message two")
  .unwrap();

// share1 and share2 are public SignatureShare values. With the corresponding
// challenges and Lagrange coefficient, solve:
//   secret = (share1 - share2) / ((c1 - c2) * lagrange)
```

`share1` and `share2` are emitted as ordinary signature shares, while both executions were reconstructed from the same `CachedPreprocess` because no consumed-state check exists. [9](#0-8)

### Citations

**File:** crypto/frost/src/sign.rs (L170-180)
```rust

  fn preprocess<R: RngCore + CryptoRng>(
    self,
    rng: &mut R,
  ) -> (Self::SignMachine, Preprocess<C, A::Addendum>) {
    let mut seed = CachedPreprocess(Zeroizing::new([0; 32]));
    rng.fill_bytes(seed.0.as_mut());
    self.seeded_preprocess(seed)
  }
}

```

**File:** crypto/frost/src/sign.rs (L246-313)
```rust
pub struct AlgorithmSignMachine<C: Curve, A: Algorithm<C>> {
  params: Params<C, A>,
  seed: CachedPreprocess,

  pub(crate) nonces: Vec<Nonce<C>>,
  // Skips the preprocess due to being too large a bound to feasibly enforce on users
  #[zeroize(skip)]
  pub(crate) preprocess: Preprocess<C, A::Addendum>,
  pub(crate) blame_entropy: [u8; 32],
}

impl<C: Curve, A: Algorithm<C>> SignMachine<A::Signature> for AlgorithmSignMachine<C, A> {
  type Params = A;
  type Keys = ThresholdKeys<C>;
  type Preprocess = Preprocess<C, A::Addendum>;
  type SignatureShare = SignatureShare<C>;
  type SignatureMachine = AlgorithmSignatureMachine<C, A>;

  fn cache(self) -> CachedPreprocess {
    self.seed
  }

  fn from_cache(
    algorithm: A,
    keys: ThresholdKeys<C>,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    AlgorithmMachine::new(algorithm, keys).seeded_preprocess(cache)
  }

  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
  }

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

**File:** crypto/frost/src/sign.rs (L447-466)
```rust
  fn complete(
    self,
    mut shares: HashMap<Participant, SignatureShare<C>>,
  ) -> Result<A::Signature, FrostError> {
    let params = self.params.multisig_params();
    validate_map(&shares, self.view.included(), params.i())?;

    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
```
