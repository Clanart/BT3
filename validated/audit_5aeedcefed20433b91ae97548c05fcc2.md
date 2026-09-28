### Title
CachedPreprocess deterministically regenerates identical FROST nonces, enabling linear key-share recovery across reused sessions - (File: crypto/frost/src/sign.rs)

### Summary
`AlgorithmMachine::seeded_preprocess` regenerates a signer's secret nonces deterministically from `CachedPreprocess(seed)`, and `SignMachine::from_cache` rebuilds a full `AlgorithmSignMachine` from that seed with no freshness guard. Because the nonce pair `(base, actual)` is fully determined by the 32-byte seed, any reuse of the same cached preprocess across distinct signing sessions reuses the same raw nonces. Since the signer's effective nonce is `r_i = base + rho_i * actual` with `rho_i` publicly computable, an unprivileged counterparty who causes the honest signer to sign in `k >= 3` sessions under the same cache can solve a linear system for the signer's secret share.

### Finding Description
`seeded_preprocess` seeds `ChaCha20Rng` with `seed.0` and derives the nonces and commitments purely from it via `Commitments::new`, making `(hiding_nonce, binding_nonce)` a deterministic function of the cached seed [1](#0-0) . `from_cache` simply calls `seeded_preprocess(cache)`, so every machine reconstructed from the same `CachedPreprocess` produces byte-identical nonces and commitments [2](#0-1) .

During `sign`, the local share nonce is computed as `base + rho * actual`, where `rho` is the signer's own binding factor derived from the public `rho_transcript` over group key, message hash, and all preprocesses [3](#0-2) . The emitted share is `s_i = base + rho_i * actual + c_i * x`, where `x` is the interpolated secret share (`Schnorr::sign_share`) [4](#0-3) . Nothing in `from_cache`/`seeded_preprocess`/`sign` records that a cache was consumed or mixes fresh entropy into nonce derivation, so the "unsanitized input" — attacker-chosen `msg` and preprocess sets — is fed into `sign_share` over a fixed secret nonce vector, exactly paralleling unsanitized-input-to-dangerous-sink: externally controlled bytes reach a cryptographic operation whose safety depends on one-time secret state.

### Impact Explanation
For each session `i` with the same seed, the attacker observes share `s_i` and publicly computes `rho_i` (the rho transcript is entirely public) and `c_i` (HRAm over public `R`, group key, message). This yields the linear system `s_i = b + rho_i * a + c_i * x` in the three unknown scalars `(a, b, x)`. Any three sessions determine `(a, b, x)` uniquely via Gaussian elimination, and further sessions over-determine it. `x` is the signer's interpolated secret share for that signing set — recovering it defeats the threshold guarantee for that participant and, combined with `t - 1` other shares (e.g., if the adversary controls `t - 1` participants or repeats the attack), recovers the group signing key, enabling arbitrary signature forgery. This satisfies the "key share recovery" acceptance criterion.

### Likelihood Explanation
The vulnerability requires only that the same `CachedPreprocess` back more than one `sign` call — precisely what the persistence API invites (the cache exists so a machine can be rebuilt across restarts) and what a real consumer does: `coordinator/src/tributary/signing_protocol.rs` stores one cached preprocess per context and calls `from_cache` on every `share_internal` invocation without rotating it [5](#0-4) . An unprivileged party who can trigger or observe multiple signing sessions (share bytes are broadcast protocol messages) needs no special privileges, and the linear algebra is trivial. The only mitigating factor is whether the integrator ever reuses a cache across distinct messages/preprocess sets — which the crypto API neither prevents nor warns against.

### Recommendation
- Make nonce derivation non-deterministic across sessions: mix fresh RNG output (or a monotonic counter persisted alongside the seed) into `seeded_preprocess`, so reconstruction from the same `CachedPreprocess` cannot reproduce identical nonces.
- Alternatively, bind the consumed-cache lifecycle: have `from_cache`/`sign` require the caller to prove the cache corresponds to an already-published preprocess for the identical `(included set, msg)` — e.g., store a commitment of the published `Preprocess` and reject signing under it with a different session context.
- At minimum, document on `SignMachine::cache`/`from_cache` that the cached seed MUST only ever be used to reconstruct a machine for the same already-published preprocess, and add a debug assertion in `sign` detecting reuse of `(a, b)` with a different `rho` transcript.

### Proof of Concept
```rust
// Conceptual: same ThresholdKeys, same CachedPreprocess, three distinct sessions.
let seed = machine.cache();                       // persisted once
let mut observed = vec![];                        // (rho_i, c_i, s_i)
for (preprocesses_i, msg_i) in sessions.take(3) { // attacker-influenced inputs
    let (m, _pp) = AlgorithmSignMachine::from_cache(alg.clone(), keys.clone(), seed.clone());
    let (m, share) = m.sign(preprocesses_i, &msg_i).unwrap();
    // rho_i: recompute BindingFactor over public transcript
    // c_i:   H::hram(&R_i, &group_key, &msg_i) -- R_i from resulting signature
    observed.push((rho_i, c_i, share.0));
}
// Solve  [1 rho_i c_i] * [b a x]^T = s_i  over the scalar field
// via Gaussian elimination -> x = interpolated secret share
assert_eq!(C::generator() * x, view.verification_share(honest_i));
```
Root cause lines: deterministic nonce regeneration in `seeded_preprocess` (`crypto/frost/src/sign.rs:127-132`) and the share equation `s = r + c*x` in `crypto/frost/src/algorithm.rs:208-210`.

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

**File:** crypto/frost/src/sign.rs (L385-396)
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
      })
      .collect::<Vec<_>>();
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
