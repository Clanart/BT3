### Title
Cached FROST preprocess reuse permits private share recovery across repeated signing attempts - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary

`SigningProtocol::share_internal` can deterministically regenerate the same FROST nonce material whenever it is called again for the same `context`, because the cached 32-byte seed is read back from `CachedPreprocesses` without being consumed or invalidated. [1](#0-0)  If the participant-controlled preprocess set changes between calls, the same underlying nonce pair is reused with a different binding factor, producing linear equations that can recover the validator’s private key share. [2](#0-1) 

### Finding Description

`preprocess_internal` derives the cached-preprocess encryption key from the signing context and local key, creates a seeded preprocess when no cache entry exists, stores that seed under `self.context`, and later loads the same seed for `AlgorithmSignMachine::from_cache`. [3](#0-2)  There is no “used”, deletion, or signing-session marker in this path, so a subsequent `share_internal` call with the same context reconstructs the same seed rather than rejecting the reuse. [1](#0-0) 

`from_cache` expands that seed with `ChaCha20Rng`, and `Commitments::new` derives the same secret nonce pair `(d, e)` from it. [4](#0-3)  `share_internal` accepts externally supplied serialized preprocesses, deserializes each one with `read_preprocess`, and passes the resulting map to `machine.sign`. [5](#0-4) 

Inside `sign`, all participant commitments and addenda are committed into the `preprocesses` transcript, which is then used to derive per-participant binding factors `rho`. [6](#0-5)  The signer’s effective nonce is `d + rho * e`; therefore changing any participant’s preprocess while reusing the same cached seed preserves `(d, e)` but changes `rho`. [7](#0-6) 

### Impact Explanation

For each successful reused signing attempt, the emitted share has the form `s_j = c_j*x_i + d_i + rho_j*e_i`, where `x_i` is the signer’s interpolated secret share and `c_j`, `rho_j`, and `s_j` are public or computable. [8](#0-7)  Two reused attempts with the same challenge immediately reveal `e_i` and then `x_i`; more generally, three attempts with distinct `(c_j, rho_j)` values provide enough independent linear equations to solve for `x_i`, `d_i`, and `e_i`. [9](#0-8) 

Recovering a validator’s threshold secret share is a direct private-key-share compromise and can contribute toward unauthorized threshold signatures or targeted validator attacks. [10](#0-9) 

### Likelihood Explanation

The vulnerable input is the participant-to-`serialized_preprocesses` map supplied to `share_internal`, and changes to those public bytes alter the committed preprocess transcript and resulting `rho`. [11](#0-10) [12](#0-11)  Any retry, repeated handling path, or parallel invocation that reaches `share_internal` twice under the same `context` therefore reuses secret nonce material while allowing attacker-visible signing inputs to differ. [13](#0-12) 

### Recommendation

Atomically consume or invalidate `CachedPreprocesses` before producing a signature share, rather than leaving the seed reusable after `from_cache`. [1](#0-0)  Alternatively, bind the cache entry to the exact participant set, serialized preprocesses, and message, and reject any `share_internal` invocation whose transcript does not match the originally cached preprocess. [12](#0-11)  A consumed-marker should preferably be written in the same database transaction as the signing output so crash recovery or replay cannot silently reuse the seed. [14](#0-13) 

### Proof of Concept

1. Instantiate a signing session for context `ctx` and allow `preprocess_internal(ctx)` to store cached seed `k`. [3](#0-2) 
2. Call `share_internal` with a valid preprocess map `P1`; the signer derives `(d, e)` from `k`, calculates `rho1`, and emits `s1 = c1*x + d + rho1*e`. [5](#0-4) [7](#0-6) 
3. Invoke `share_internal` again for the same `ctx`, but replace one participant’s serialized preprocess so that the committed transcript and binding factors differ. [11](#0-10) [15](#0-14) 
4. The same seed reconstructs `(d, e)`, while the changed preprocess set produces `rho2`, yielding `s2 = c2*x + d + rho2*e`. [4](#0-3) [9](#0-8) 
5. Repeat with a third differing preprocess map if `c1 != c2`, then solve the resulting three linear equations for the signer’s private threshold share `x`. [8](#0-7)

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L104-170)
```rust
    // Encrypt the cached preprocess as recovery of it will enable recovering the private key
    // While the DB isn't expected to be arbitrarily readable, it isn't a proper secret store and
    // shouldn't be trusted as one
    let mut encryption_key = {
      let mut encryption_key_preimage =
        Zeroizing::new(b"Cached Preprocess Encryption Key".to_vec());
      encryption_key_preimage.extend(self.context.encode());
      let repr = Zeroizing::new(self.key.to_repr());
      encryption_key_preimage.extend(repr.deref());
      Blake2s256::digest(&encryption_key_preimage)
    };
    let encryption_key_slice: &mut [u8] = encryption_key.as_mut();

    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();

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

    (machine, preprocess.serialize().try_into().unwrap())
  }

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

**File:** crypto/frost/src/sign.rs (L315-398)
```rust
    {
      // Domain separate FROST
      self.params.algorithm.transcript().domain_separate(b"FROST");
    }

    let nonces = self.params.algorithm.nonces();
    #[allow(non_snake_case)]
    let mut B = BindingFactor(HashMap::<Participant, _>::with_capacity(included.len()));
    {
      // Parse the preprocesses
      for l in &included {
        {
          self
            .params
            .algorithm
            .transcript()
            .append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
        }

        if *l == self.params.keys.params().i() {
          let commitments = self.preprocess.commitments.clone();
          commitments.transcript(self.params.algorithm.transcript());

          let addendum = self.preprocess.addendum.clone();
          {
            let mut buf = vec![];
            addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, commitments);
          self.params.algorithm.process_addendum(&view, *l, addendum)?;
        } else {
          let preprocess = preprocesses.remove(l).unwrap();
          preprocess.commitments.transcript(self.params.algorithm.transcript());
          {
            let mut buf = vec![];
            preprocess.addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, preprocess.commitments);
          self.params.algorithm.process_addendum(&view, *l, preprocess.addendum)?;
        }
      }

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

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
    }

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
```

**File:** crypto/frost/src/algorithm.rs (L201-210)
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
```
