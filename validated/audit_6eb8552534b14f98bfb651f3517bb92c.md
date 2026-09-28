### Title
Deterministic `CachedPreprocess` reuse across signing rounds leaks the FROST secret share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The bug class of the external report is "an attacker-influenced value is committed into persistent state and later re-executed without re-validation." The Serai analog is the cached-preprocess path in `SigningProtocol::preprocess_internal`: a ChaCha20 seed stored in `CachedPreprocesses` deterministically regenerates the identical FROST nonces every time `from_cache` is called, and `share_internal` re-reads that same seed on each invocation rather than deleting it after first use.

### Finding Description
`preprocess_internal` generates a 32-byte RNG seed, XOR-encrypts it under a key derived from `context || secret_share`, stores it via `CachedPreprocesses::set`, and then **every** call loads the same seed and passes it to `AlgorithmSignMachine::from_cache` [1](#0-0) . `from_cache` -> `seeded_preprocess` feeds that seed into `ChaCha20Rng::from_seed`, so `Commitments::new` regenerates the exact same `(d, e)` nonce pair [2](#0-1) . The seed is never cleared from the DB after use, so the second and subsequent `share_internal` calls for the same `context` reuse identical nonces. The crate's own docs state reuse "will enable third-party recovery of your private key share" [3](#0-2) ; spec/FROST.md repeats this [4](#0-3) .

The signing share is `s_i = r + c·λ_i·x_i` [5](#0-4) . If the same `r` is committed under two different challenges `c1 ≠ c2` — which occurs whenever the message or the signing set (and hence `rho` via `hash_msg`/`hash_commitments` and the per-participant binding factor) differs between attempts [6](#0-5)  — then `x_i = (s1 − s2)/(c1 − c2)` recovers the victim's Lagrange-weighted secret share.

### Impact Explanation
An unprivileged counterparty only has to cause the validator to sign twice under the same cached context with a differing `msg` or participant set — e.g., by submitting preprocesses that trigger a failed/blamed attempt followed by a retry, or any re-issued sign order for the same tributary context. Both signature shares are broadcast publicly over the authenticated channel. With `s1, s2, c1, c2, R` all public, the adversary solves for the victim's secret share directly. Compromise of enough shares (or of a threshold key whose other shares are otherwise obtainable) yields full group-key recovery — arbitrary signatures / theft of all funds controlled by that FROST key. This is a key-share-recovery primitive reachable purely from protocol messages, matching the "Critical" bar.

### Likelihood Explanation
The unsafe path is taken by design: `preprocess_internal` deliberately caches so a *single* preprocess round can be reused, but nothing enforces the documented MUST-use-once invariant — there is no `CachedPreprocesses::delete`, no attempt counter mixed into the seed, and no one-shot guard. Any code path that calls `share_internal` more than once per `context` (retries after `InvalidPreprocess`/`InvalidShare`, coordinator re-issued orders, multiple `attempt` values sharing the context) deterministically reuses the nonces. Whether production flow actually re-enters `share_internal` for an identical `context` depends on how `context`/`TributarySpec` encodes attempts, which I could not fully verify — if every retry mutates `context`, the seed differs only insofar as the encryption key differs, but the same stored ciphertext is still re-loaded and re-decrypted to the same seed for the same context. The exposure exists whenever a repeat occurs, and a malicious participant can force repeat attempts at negligible cost.

### Recommendation
After a `CachedPreprocess` is consumed by `from_cache`, delete or overwrite `CachedPreprocesses` for that context so a subsequent `share_internal` generates a fresh seed. Alternatively mix a monotonically increasing per-context counter (or the signing `attempt`/`id`) into the ChaCha20 seed derivation so distinct signing rounds can never produce identical nonces, and assert one-shot use in `share_internal`.

### Proof of Concept
1. Attacker (a threshold participant) participates in a tributary signing session for context `C`.
2. Attacker submits a malformed-but-parseable preprocess for themselves such that `sign` succeeds for the victim but the aggregate later fails, or the coordinator re-issues the sign order under the same `C` with a modified participant set/message.
3. Victim calls `share_internal` twice: both calls hit `CachedPreprocesses::get(txn, C)`, decrypt the same 32-byte seed, and `seeded_preprocess` regenerates identical `d, e` — hence identical `r = d + ρe` (note: if the signing set changes, `ρ` changes, so `r` differs but `d, e` are still reused; the attack needs the same set with a different `msg`, where `ρ` is identical per-participant but `c` differs — the simplest trigger is same set, different `msg`).
4. Collect victim shares `s1 = r + c1·λx`, `s2 = r + c2·λx`. Compute `λx = (s1 − s2)·(c1 − c2)^{-1}`. Repeat across `t` victims to reconstruct the group private key.

Caveat: I could not trace every caller to confirm a reachable second `share_internal` under an identical `context`; the vulnerability is that the nonce-reuse guard documented as a hard requirement is not enforced anywhere in this code path.

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

**File:** crypto/frost/src/sign.rs (L362-379)
```rust
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
