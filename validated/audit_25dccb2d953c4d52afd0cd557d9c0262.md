### Title
Context-keyed `CachedPreprocess` reuse enables FROST nonce reuse and validator key recovery — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary

The external report (CVE-2024-10451) describes a class where a sensitive value is captured once and then persisted/replayed as a "default" outside the context it was generated for. The analog in Serai: `SigningProtocol::preprocess_internal` deterministically re-derives the FROST preprocess (i.e., the secret nonces `d, e`) from a seed cached in the DB keyed **only** by `context = (b"DkgConfirmer", attempt)` — not by the message or the peers' commitments. Both `share()` and `complete()` rebuild an `AlgorithmSignMachine` from the same cached seed and each emit a Schnorr signature share. If the two calls are ever made with the same peer preprocesses but a different message (different `key_pair`), or are otherwise invoked on divergent state, the identical effective nonce is signed under two distinct challenges — yielding the validator's MuSig secret share directly.

### Finding Description

`preprocess_internal` computes an encryption key from `context` + the validator key, and caches the ChaCha20 seed for the preprocess under `CachedPreprocesses::get(self.txn, &self.context)` [1](#0-0) . `AlgorithmSignMachine::from_cache` → `seeded_preprocess` regenerates the *exact same* nonces from that seed via `ChaCha20Rng::from_seed(*seed.0)` [2](#0-1) .

The context bound to this reused secret material is only `(b"DkgConfirmer", self.attempt)` [3](#0-2) . Yet `share_internal` (used by both `share` and `complete`) signs a message that additionally depends on `key_pair` and the caller-supplied `preprocesses` map [4](#0-3) . `DkgConfirmer::complete` calls `share_internal` a **second time** with its own `preprocesses`/`key_pair` arguments [5](#0-4)  — there is no check that these match what `share` already signed, and the file's own header admits the on-chain verification of the presumed preprocess is an unimplemented `TODO` [6](#0-5) .

Per the file's own safety analysis, nonce reuse happens exactly when "the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" [7](#0-6)  — the safety argument rests entirely on BFT ordering plus a check that does not exist. With the same peer commitments and a different `key_pair` (the message parameter), the binding factor `rho` and effective nonce `n = d + rho·e` are identical, while the challenge `c` differs.

### Impact Explanation

Each share is `z = n + λ·s·c` over the same effective nonce `n`. Two shares `z1, z2` for the same `n` under distinct challenges `c1 ≠ c2` give `s = (z1 − z2) / (λ·(c1 − c2))`: full recovery of the validator's private key share — the root-of-trust key used to confirm DKG results on Substrate. This is precisely the "third-party recovery of your private key share" the FROST docs warn about for reused preprocesses [8](#0-7) .

### Likelihood Explanation

Reachability requires `share`/`complete` (or repeated `share`) to be invoked under the same `attempt` context with different messages or commitment sets — i.e., inconsistent tributary handling of `key_pair` or preprocess lists between calls. That requires either a logic flaw in the coordinator's message handling or a rebuild/restore path that re-executes with divergent state; the code explicitly acknowledges partial-rebuild bounds are merely "accepted" and the guard is a TODO. This places the preconditions mostly behind consensus-layer assumptions rather than a purely external attacker, which lowers likelihood — but the cryptographic consequence (key recovery) is maximal and the missing check is acknowledged in-code. **Severity: Medium.**

### Recommendation

- Bind the cached preprocess context to the full signing inputs: derive the `CachedPreprocesses` key from `(context, msg_hash, sorted commitments hash)` so any divergence produces a *fresh* seed rather than reused nonces — or refuse to sign and mark the attempt poisoned when a cache entry already exists for a different input tuple.
- Implement the documented TODO: before publishing a share, verify the locally presumed preprocess/commitments match what was finalized on-chain for that attempt.
- Store a "share emitted" marker per context with the `(msg, commitments)` it was emitted for; abort `complete`/`share` if parameters differ.

### Proof of Concept

Conceptual (coordinator-side):

1. During DKG attempt `a`, tributary handling invokes `DkgConfirmer::share(preprocesses, key_pair_A)`. `preprocess_internal` loads seed `S` under context `(b"DkgConfirmer", a)`, regenerates nonces `(d, e)`, and emits share `z1 = (d + ρe) + λ·s·c1` for `msg_A = set_keys_message(set, removed, key_pair_A)`.
2. Later (e.g., a rebuilt process following a divergent tributary state, or a duplicate message-handling path), `DkgConfirmer::complete(preprocesses, key_pair_B, shares)` calls `share_internal` again → same seed `S`, same `d, e`; with identical `preprocesses`, `ρ` is unchanged but `msg_B ≠ msg_A` yields `c2 ≠ c1`, emitting `z2 = (d + ρe) + λ·s·c2`.
3. An observer collects both public shares and computes `s = (z1 − z2)·(λ·(c1 − c2))^{-1}`, recovering the validator's MuSig secret key, then forges `set_keys` confirmations.

Uncertain aspects I could not fully verify within the iteration budget: the exact tributary message paths that supply `key_pair`/`preprocesses` to `share` vs `complete` (whether divergent values are reachable without a malicious validator), and whether a second `SigningProtocol` context type exists elsewhere in the coordinator.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-35)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L50-54)
```rust
  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
```

**File:** coordinator/src/tributary/signing_protocol.rs (L107-145)
```rust
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-302)
```rust
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
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L312-327)
```rust
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
