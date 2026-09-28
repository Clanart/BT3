### Title
Deterministic `CachedPreprocess` reuse produces identical FROST nonces across multiple signing sessions under one context, enabling remote secret-share/key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` caches a 32-byte preprocess seed per `context` and rebuilds the signing machine from that same seed on every invocation. Because `AlgorithmMachine::seeded_preprocess` feeds the seed into `ChaCha20Rng::from_seed` deterministically, every `sign()` call under the same context derives the exact same nonce pair `(d, e)` and publishes the exact same commitments `D, E`. If the same context is used for more than one signing round (reattempts, parallel sessions, or an attacker-controlled change to the preprocess set), the resulting shares are linear equations in the same nonce scalars, and an unprivileged participant who collects enough such shares can solve for the victim's secret share — direct analog of CVE-2016-2788's "unprivileged remote input reaches an unintended privileged action" class, mapped onto Serai as unauthenticated preprocessing input reaching nonce reuse and key recovery.

### Finding Description
In `preprocess_internal`, the cached seed is fetched once per `context` and reused verbatim:

```rust
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
let mut cached: Zeroizing<[u8; 32]> = Zeroizing::new(cached);
for b in 0 .. 32 {
  cached[b] ^= encryption_key_slice[b];
}
let (machine, preprocess) =
  AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
``` [1](#0-0) 

`share_internal` calls `preprocess_internal` on every signing request for that context, then signs attacker-supplied preprocess bytes deserialized via `read_preprocess`:

```rust
let machine = self.preprocess_internal(participants).0;
...
machine.read_preprocess(&mut serialized_preprocesses.remove(&participant).unwrap().as_slice())
``` [2](#0-1) 

`from_cache` → `seeded_preprocess` regenerates nonces purely from the seed:

```rust
let mut rng = ChaCha20Rng::from_seed(*seed.0);
let (nonces, commitments) = Commitments::new::<_>(
  &mut rng,
  params.keys.original_secret_share(),
  &params.algorithm.nonces(),
);
``` [3](#0-2) 

The docs on `CachedPreprocess`/`from_cache` state explicitly that a cached preprocess "MUST only be used once" and that reuse "will presumably cause the signer to leak their secret share" — yet the coordinator intentionally reuses the cache for every `share_internal`/`preprocess_internal` under a fixed context, and nothing in `share_internal` burns or rotates the seed per message or per signing set. [4](#0-3) 

In `sign()`, the share scalar is `share = d + e·rho_i + λ_i·c·secret_share` where `rho_i` is the binding factor from `B.binding_factors` and `c` is the algorithm challenge over `msg` and the aggregate `Rs`. The nonces `(d, e)` are identical whenever the seed is identical; only `rho`, `c`, and `λ` vary with the (attacker-influenced) preprocess set and message. [5](#0-4) 

### Impact Explanation
Each signature share published under the same context is one linear equation in the three unknowns `(d, e, secret_share)` (all other quantities — `rho_i`, the per-signer challenge `c_i`, and the Lagrange/interpolation factor `λ_i` — are publicly computable from the broadcast commitments, the included set, and the message). With three shares produced under the same context for three different `(msg, included-set)` combinations, an unprivileged participant solves a 3×3 linear system over the scalar field and recovers the victim validator's FROST secret share. That is key-share recovery — the most severe accepted impact — reached purely through public protocol messages (preprocess bytes and reattempts) that an unprivileged threshold participant can cause the signer to process.

### Likelihood Explanation
- Reachability: `CoordinatorMessage::SubstratePreprocesses`/`Reattempt`-style flows and parallel signing sessions under one context feed attacker-influenced preprocess maps into `share_internal`; the attacker controls the included set composition and the message indirectly, guaranteeing distinct `rho`/`c` per round while nonces stay fixed.
- Determinism: `ChaCha20Rng::from_seed(*seed.0)` makes nonce reuse unconditional whenever the context collides — no probabilistic assumption needed.
- The only gating condition is the coordinator reusing `self.context` across more than two `sign()` executions; reattempt and multi-attempt flows exist precisely to make this realistic. Because the seed is only written once (`if CachedPreprocesses::get(...).is_none()`), there is no per-sign freshness.

### Recommendation
Never rebuild an `AlgorithmSignMachine` from the same `CachedPreprocess` for a second `sign()`. Either:
1. Derive the per-sign seed as `H(seed || msg || included_set || attempt)` so each signing round gets independent nonces, or
2. Treat the cache as single-use: delete `CachedPreprocesses[context]` when the first preprocess is consumed and refuse to sign twice under one context, forcing a fresh `preprocess()` call (fresh `OsRng` seed) per signing attempt.

### Proof of Concept
1. Attacker participates in a FROST session where the coordinator's `SigningProtocol` is keyed by `context` C.
2. Round 1: attacker submits a valid preprocess, the set `S1 = {victim, attacker, …}` and message `m1` are signed; victim broadcasts share `s1 = d + e·rho1 + λ1·c1·x`.
3. Round 2 (reattempt or second session under C): different `S2` or `m2`; same seed → same `(d, e)`; share `s2 = d + e·rho2 + λ2·c2·x`.
4. Round 3 likewise yields `s3`. Attacker computes `rho_i` via `hash_binding_factor` over the public rho transcript, `c_i` via the algorithm HRAM, and `λ_i` via `Interpolation::interpolation_factor`, then solves the 3×3 linear system for `x` — the victim's secret share — recovering threshold-signing capability for the victim's index.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L137-145)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-168)
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
```

**File:** crypto/frost/src/sign.rs (L127-133)
```rust
    let mut rng = ChaCha20Rng::from_seed(*seed.0);
    let (nonces, commitments) = Commitments::new::<_>(
      &mut rng,
      params.keys.original_secret_share(),
      &params.algorithm.nonces(),
    );
    let addendum = params.algorithm.preprocess_addendum(&mut rng, &params.keys);
```

**File:** crypto/frost/src/sign.rs (L209-225)
```rust
  /// Cache this preprocess for usage later.
  ///
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
  fn cache(self) -> CachedPreprocess;

  /// Create a sign machine from a cached preprocess.
  ///
  /// After this, the preprocess must be deleted so it's never reused. Any reuse will presumably
  /// cause the signer to leak their secret share.
  fn from_cache(
    params: Self::Params,
    keys: Self::Keys,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess);

```

**File:** crypto/frost/src/sign.rs (L385-398)
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

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
```
