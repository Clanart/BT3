### Title
Cached DKG confirmation preprocesses reuse FROST nonces across messages - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` caches one deterministic FROST seed per `("DkgConfirmer", attempt)` context, decrypts it, and reconstructs a signing machine from the same seed every time `share_internal` runs. It never deletes the cache or binds the cached nonce to the message being confirmed. Two shares produced for different `key_pair` messages under the same attempt and peer preprocess set therefore reuse the same nonce, allowing recovery of the validator’s signing share.

### Finding Description
`preprocess_internal` only creates a new preprocess if the context key is absent; otherwise it reloads the stored seed and calls `AlgorithmSignMachine::from_cache` again. [1](#0-0)  `share_internal` invokes this cached path each time it signs a caller-selected `msg`. [2](#0-1) 

The cached seed initializes `ChaCha20Rng`, which deterministically derives the nonce scalars and their commitments. [3](#0-2)  Reconstructing from that seed therefore returns the identical `d` and `e` nonce pair rather than a fresh preprocess.

When the same participants and peer preprocess bytes are supplied, the binding factor is also unchanged because it is derived from the group key, message hash, and committed preprocess transcript. [4](#0-3)  The effective nonce remains `d + rho * e`, while the message changes only the Schnorr challenge. [5](#0-4) 

The resulting share is the standard Schnorr response `s = r + c*x`, where `x` is the participant’s interpolated threshold share. [6](#0-5)  Thus two responses for distinct messages satisfy `s1 - s2 = (c1 - c2) * x`, revealing `x`.

### Impact Explanation
An unprivileged participant able to cause the validator to produce two confirmation shares for different `key_pair` values in the same attempt can recover that validator’s interpolated signing share. In the MuSig construction used here, the private key is placed directly into `ThresholdKeys`, with public constant interpolation factors, so the recovered share can be divided by its known interpolation factor to recover the underlying validator private key. [7](#0-6) 

This is key-share recovery, not merely signature malleability: possession of the recovered share lets the attacker participate as that validator in subsequent threshold/MuSig operations.

### Likelihood Explanation
The vulnerable state is keyed only by `("DkgConfirmer", attempt)`, so every share calculation for the same DKG attempt reuses the nonce. [8](#0-7)  The attacker does not need access to the database, the cached seed, or the private key; they only need two otherwise valid signing evaluations for the same attempt and peer commitment set but different confirmation messages.

A second share can be obtained either from another `share` response or by deriving the victim’s second share from a completed aggregate signature after subtracting all other supplied shares.

### Recommendation
Make cached preprocesses strictly single-use. Atomically remove `CachedPreprocesses` before reconstructing the `AlgorithmSignMachine`, and refuse to sign if reconstruction is attempted for an already-consumed context. The cache key should additionally bind the full confirmation message and participant/preprocess set, so retries with a different message cannot reuse the nonce.

### Proof of Concept
1. Fix an attempt `a`, participant list, and peer preprocess map `P`.
2. Cause the validator to call `share_internal(P, key_pair_1)`, producing `s1`.
3. Cause another signing evaluation for the same attempt with `key_pair_2 != key_pair_1` and the same `P`, producing or deriving `s2`.
4. Because the cached seed is unchanged, both shares use the same nonce `r`.
5. Compute:
   `x = (s1 - s2) * inverse(c1 - c2)`.
6. Divide `x` by the public MuSig interpolation/binding factor to recover the validator’s private key.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L123-145)
```rust
    if CachedPreprocesses::get(self.txn, &self.context).is_none() {
      let (machine, _) =
        AlgorithmMachine::new(algorithm.clone(), keys.clone()).preprocess(&mut OsRng);

      let mut cache = machine.cache();
      assert_eq!(cache.0.len(), 32);
      #[allow(clippy::needless_range_loop)]
      for b in 0 .. 32 {
        cache.0[b] ^= encryption_key_slice[b];
      }

      CachedPreprocesses::set(self.txn, &self.context, &cache.0);
    }

    let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
    let mut cached: Zeroizing<[u8; 32]> = Zeroizing::new(cached);
    #[allow(clippy::needless_range_loop)]
    for b in 0 .. 32 {
      cached[b] ^= encryption_key_slice[b];
    }
    encryption_key_slice.zeroize();
    let (machine, preprocess) =
      AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

**File:** coordinator/src/tributary/signing_protocol.rs (L150-170)
```rust
  fn share_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
    mut serialized_preprocesses: HashMap<Participant, Vec<u8>>,
    msg: &[u8],
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let machine = self.preprocess_internal(participants).0;

    let mut participants = serialized_preprocesses.keys().copied().collect::<Vec<_>>();
    participants.sort();
    let mut preprocesses = HashMap::new();
    for participant in participants {
      preprocesses.insert(
        participant,
        machine
          .read_preprocess(&mut serialized_preprocesses.remove(&participant).unwrap().as_slice())
          .map_err(|_| participant)?,
      );
    }

    let (machine, share) = machine.sign(preprocesses, msg).map_err(|e| match e {
```

**File:** coordinator/src/tributary/signing_protocol.rs (L274-301)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }

  fn preprocess_internal(&mut self) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    self.signing_protocol().preprocess_internal(&participants)
  }
  // Get the preprocess for this confirmation.
  pub(crate) fn preprocess(&mut self) -> [u8; 64] {
    self.preprocess_internal().1
  }

  fn share_internal(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    let preprocesses =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, preprocesses).1;
    let msg = set_keys_message(
      &self.spec.set(),
      &self.removed.iter().map(|key| Public::from(key.to_bytes())).collect::<Vec<_>>(),
      key_pair,
    );
    self.signing_protocol().share_internal(&participants, preprocesses, &msg)
```

**File:** crypto/frost/src/sign.rs (L127-139)
```rust
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
```

**File:** crypto/frost/src/sign.rs (L361-371)
```rust
      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);
```

**File:** crypto/frost/src/sign.rs (L385-394)
```rust
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

**File:** crypto/dkg/musig/src/lib.rs (L117-160)
```rust
  context: [u8; 32],
  private_key: Zeroizing<C::F>,
  keys: &[C::G],
) -> Result<ThresholdKeys<C>, MusigError<C>> {
  let our_pub_key = C::generator() * private_key.deref();
  let Some(our_i) = keys.iter().position(|key| *key == our_pub_key) else {
    Err(MusigError::DkgError(DkgError::NotParticipating))?
  };

  let keys_len: u16 = check_keys::<C>(keys)?;

  let params = ThresholdParams::new(
    keys_len,
    keys_len,
    // The `+ 1` won't fail as `keys.len() <= u16::MAX`, so any index is `< u16::MAX`
    Participant::new(
      u16::try_from(our_i).expect("keys.len() <= u16::MAX yet index of keys > u16::MAX?") + 1,
    )
    .expect("i + 1 != 0"),
  )
  .map_err(MusigError::DkgError)?;

  let transcript = binding_factor_transcript::<C>(context, keys_len, keys);
  let mut binding_factors = Vec::with_capacity(keys.len());
  let mut multiexp = Vec::with_capacity(keys.len());
  let mut verification_shares = HashMap::with_capacity(keys.len());
  for (i, key) in (1 ..= keys_len).zip(keys.iter().copied()) {
    let binding_factor = binding_factor::<C>(transcript.clone(), i);
    binding_factors.push(binding_factor);
    multiexp.push((binding_factor, key));

    let i = Participant::new(i).expect("non-zero u16 wasn't a valid Participant index?");
    verification_shares.insert(i, key);
  }
  let group_key = multiexp::multiexp(&multiexp);
  debug_assert_eq!(our_pub_key, verification_shares[&params.i()]);
  debug_assert_eq!(musig_key_vartime::<C>(context, keys), Ok(group_key));

  ThresholdKeys::new(
    params,
    Interpolation::Constant(binding_factors),
    private_key,
    verification_shares,
  )
```
