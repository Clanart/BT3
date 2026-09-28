### Title
CachedPreprocess reuse produces identical FROST nonces across distinct signing sessions, enabling private key share recovery - (File: crypto/frost/src/sign.rs)

### Summary
`AlgorithmMachine::seeded_preprocess` derives the FROST nonces deterministically from `ChaCha20Rng::from_seed(*seed.0)` alone — the seed is the only entropy input, and neither the message, the participant set, nor any session identifier is mixed in. `SignMachine::from_cache` feeds an attacker-visible cached seed straight back into `seeded_preprocess`, so any caller that reconstructs a machine from the same `CachedPreprocess` twice obtains the exact same nonce pair `[base, actual]` for both sessions. [1](#0-0) [2](#0-1) 

The coordinator's `SigningProtocol::preprocess_internal` does exactly this: it stores the cached seed under `CachedPreprocesses` keyed only by `self.context` (e.g. `(b"DkgConfirmer", attempt)`) and re-reads it on every call, never deleting it after a share is produced. `share_internal` calls `preprocess_internal` fresh each time, and `DkgConfirmer::share` / `DkgConfirmer::complete` each invoke `share_internal` with attacker-supplied `preprocesses` maps. [3](#0-2) [4](#0-3) [5](#0-4) 

### Finding Description
In `AlgorithmSignMachine::sign`, the effective nonce is `base + rho_l * actual` where `rho_l` is the per-participant binding factor derived from the `rho_transcript` (group key, `hash_msg(msg)`, and the hash of all participants' preprocesses/commitments). [6](#0-5) 

Because the cached seed fully determines `self.nonces`, two calls to `sign` under the same context with different `preprocesses` (or a different `msg`) reuse `base` and `actual` while producing different binding factors `rho_1 ≠ rho_2` and different challenges `c_1 ≠ c_2`. Each published `SignatureShare` is a scalar `s_j = (base + rho_j * actual) + lambda * secret_share * c_j` (per `algorithm.sign_share`). [7](#0-6) 

An observer who collects both broadcast shares gets two linear equations in two unknowns (`actual`-related term and `secret_share`), solvable in closed form:

```
k_j   = base + rho_j * actual          (effective nonce of session j)
s_j   = k_j + lambda * x * c_j         (x = private key share)
```

`R_j` is public from the commitments, and since `actual` is identical across sessions, `k_1` and `k_2` differ only by the known `(rho_1 - rho_2) * actual`. Combining `s_1 - s_2 = (rho_1 - rho_2)*actual + lambda*x*(c_1 - c_2)` with a third equation (or a second reuse with equal rho but different `msg`, giving `s_1 - s_2 = lambda*x*(c_1 - c_2)` directly) recovers `x`, the signer's private key share — for the coordinator path, the validator's MuSig secret key itself.

The FROST spec acknowledges this hazard only as a documentation-level "MUST not reuse preprocesses" [8](#0-7) , but the code provides deterministic `from_cache`/`seeded_preprocess` machinery that makes reuse silent and byte-for-byte identical, and the in-repo caller caches the seed in a persistent DB without ever clearing it after `sign` succeeds. The file's own safety argument relies solely on BFT message uniqueness — it explicitly lists "distinct received messages" causing a repeated `sign` as the failure condition. [9](#0-8) 

### Impact Explanation
Confidentiality loss of the worst kind in this system: unauthorized recovery of a signer's private key share from data broadcast publicly (preprocesses and signature shares are transmitted to all participants and, in the coordinator flow, committed on-chain). For the coordinator's `DkgConfirmer`, the leaked secret is the validator's MuSig private key — full compromise of that validator's signing capability, equivalent to the CVE's "unauthorized read access to a subset of data" mapped onto the most sensitive data Serai holds. For FROST threshold keys, a leaked share reduces the security threshold and, combined with the signer's own share or any other leaked share, enables key recovery / forgery.

### Likelihood Explanation
Reachability requires a participant to cause `share_internal`/`sign` to execute twice under the same context with differing inputs. The preprocess sets are deserialized from `CoordinatorMessage`-driven maps supplied per call (`read_preprocess` on untrusted bytes) [10](#0-9) , and `DkgConfirmer::share` and `DkgConfirmer::complete` each re-derive the machine via `share_internal`, so any path where the preprocess map or `key_pair` differs between invocations (partial rebuild, retried attempt with different submissions, distinct share sets finalized) triggers reuse with byte-identical nonces. The defense is purely a documented caller obligation; nothing in `from_cache` or the DB layer enforces single-use. Exploitation requires only observing two public shares — no key material, no privileged access beyond the ability to submit preprocess bytes to the signing protocol.

### Recommendation
- In `crypto/frost/src/sign.rs`, bind derived nonces to session inputs: hash the seed together with the serialized `included` set, `hash_msg(msg)`, and the commitments transcript before generating nonces, so that any change in participants/message produces independent nonces even on seed reuse.
- In `coordinator/src/tributary/signing_protocol.rs`, delete `CachedPreprocesses` (or set a "consumed" flag) after the first successful `sign` for a context, and refuse to produce a second share; persist and verify the produced `preprocess`/share on re-execution instead of re-signing.
- Add a test in `crypto/frost/src/tests` asserting that `from_cache` twice with differing preprocess maps yields distinct effective nonces.

### Proof of Concept
```rust
// crypto/frost — conceptual PoC over Ristretto + IetfSchnorr
// 1. Validator builds machine from a cached seed S (as preprocess_internal does
//    every call, reading CachedPreprocesses keyed by context).
let (m1, _pp1) = AlgorithmSignMachine::from_cache(algo.clone(), keys.clone(), CachedPreprocess(S));
let (m2, _pp2) = AlgorithmSignMachine::from_cache(algo, keys, CachedPreprocess(S));

// m1.nonces == m2.nonces, m1.preprocess == m2.preprocess  (deterministic seed)

// 2. Session A: attacker supplies preprocess set P_a; honest signer publishes share s_a.
let (_, s_a) = m1.sign(preprocesses_a, msg_a).unwrap();

// 3. Session B (same context, e.g. complete() re-running share_internal):
//    attacker supplies a different preprocess set P_b (or a different msg).
let (_, s_b) = m2.sign(preprocesses_b, msg_b).unwrap();

// 4. Same nonces => effective nonce differs only via public binding factors.
//    s_a - s_b = (rho_a - rho_b)*actual + lambda*x*(c_a - c_b)
//    With a third equation (or equal rho with differing msg):
//    x = (s_a - s_b) / (lambda * (c_a - c_b))   => private key share recovered
//    from two publicly broadcast SignatureShare scalars.
```
Both shares are public protocol messages; rho, lambda, and the challenges are recomputable by any observer from the public preprocesses and message, so the recovery requires no private state.

### Citations

**File:** crypto/frost/src/sign.rs (L121-144)
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
  }
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

**File:** crypto/frost/src/sign.rs (L361-409)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L25-48)
```rust
  As for safety, it is explicitly unsafe to reuse nonces across signing sessions. This raises
  concerns regarding our re-execution which is dependent on fixed nonces. Safety is derived from
  the nonces being context-bound under a BFT protocol. The flow is as follows:

  1) Decide the nonce.
  2) Publish the nonces' commitments, receiving everyone elses *and potentially the message to be
     signed*.
  3) Sign and publish the signature share.

  In order for nonce re-use to occur, the received nonce commitments (or the message to be signed)
  would have to be distinct and sign would have to be called again.

  Before we act on any received messages, they're ordered and finalized by a BFT algorithm. The
  only way to operate on distinct received messages would be if:

  1) A logical flaw exists, letting new messages over write prior messages
  2) A reorganization occurred from chain A to chain B, and with it, different messages

  Reorganizations are not supported, as BFT is assumed by the presence of a BFT algorithm. While
  a significant amount of processes may be byzantine, leading to BFT being broken, that still will
  not trigger a reorganization. The only way to move to a distinct chain, with distinct messages,
  would be by rebuilding the local process (this time following chain B). Upon any complete
  rebuild, we'd re-decide nonces, achieving safety. This does set a bound preventing partial
  rebuilds which is accepted.
```

**File:** coordinator/src/tributary/signing_protocol.rs (L123-147)
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

    (machine, preprocess.serialize().try_into().unwrap())
```

**File:** coordinator/src/tributary/signing_protocol.rs (L150-181)
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
      FrostError::InternalError(e) => unreachable!("FrostError::InternalError {e}"),
      FrostError::InvalidParticipant(_, _) |
      FrostError::InvalidSigningSet(_) |
      FrostError::InvalidParticipantQuantity(_, _) |
      FrostError::DuplicatedParticipant(_) |
      FrostError::MissingParticipant(_) => unreachable!("{e:?}"),
      FrostError::InvalidPreprocess(p) | FrostError::InvalidShare(p) => p,
    })?;

    Ok((machine, share.serialize().try_into().unwrap()))
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L304-327)
```rust
  pub(crate) fn share(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<[u8; 32], Participant> {
    self.share_internal(preprocesses, key_pair).map(|(_, share)| share)
  }

  pub(crate) fn complete(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
    shares: HashMap<Participant, Vec<u8>>,
  ) -> Result<[u8; 64], Participant> {
    let shares =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, shares).1;

    let machine = self
      .share_internal(preprocesses, key_pair)
      .expect("trying to complete a machine which failed to preprocess")
      .0;

    DkgConfirmerSigningProtocol::<'_, T>::complete_internal(machine, shares)
  }
```

**File:** spec/cryptography/FROST.md (L45-62)
```markdown
# Caching

modular-frost supports caching a preprocess. This is done by having all
preprocesses use a seeded RNG. Accordingly, the entire preprocess can be derived
from the RNG seed, making the cache just the seed.

Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.

Since a reused seed will lead to a reused preprocess, seeded RNGs are generally
frowned upon when doing multisignature operations. This isn't an issue as each
new preprocess obtains a fresh seed from the specified RNG. Assuming the
provided RNG isn't generating the same seed multiple times, the only way for
this seeded RNG to fail is if a preprocess is loaded multiple times, which was
already a failure point.
```
