### Title
One-time FROST preprocess seed is persisted and reused across `sign` invocations, enabling nonce-reuse recovery of the secret key share - (File: crypto/frost/src/sign.rs, coordinator/src/tributary/signing_protocol.rs)

### Summary
CVE-2017-18220 is a use-after-free: a resource that must be consumed/closed once (a blob in `CloseBlob`) is kept alive and dereferenced again through attacker-controlled input. The Serai analog is the one-time FROST preprocess seed. `AlgorithmSignMachine::from_cache` / `seeded_preprocess` deterministically regenerate the *same* nonces from a cached 32-byte seed, and the API contract states the cache "must be deleted so it's never reused" — the cryptographic equivalent of a freed resource. The coordinator in `SigningProtocol::preprocess_internal` persists that seed in `CachedPreprocesses` keyed only by `context` and **never deletes it after `sign`**, so every subsequent signing flow under the same context resurrects the identical nonces — a use-after-free of a one-time value that an unprivileged participant can reach via the preprocess/share messages it submits.

### Finding Description
In `crypto/frost/src/sign.rs`, `AlgorithmMachine::seeded_preprocess` derives all FROST nonces from `ChaCha20Rng::from_seed(*seed.0)`:

- `seeded_preprocess` (lines 121-144): `let mut rng = ChaCha20Rng::from_seed(*seed.0);` then `Commitments::new::<_>(&mut rng, params.keys.original_secret_share(), &params.algorithm.nonces())` — the nonce scalars `d`, `e` are a pure function of the seed. Same seed ⇒ same nonces, regardless of participants or message.
- The API contract on `CachedPreprocess` (lines 83-87) and `SignMachine::from_cache` (lines 216-224): *"A preprocess MUST only be used once. Reuse will enable third-party recovery of your private key share… the preprocess must be deleted so it's never reused."*

The coordinator violates this contract in `coordinator/src/tributary/signing_protocol.rs`:

- `preprocess_internal` (lines 100-148) writes the (XOR-encrypted) seed into `CachedPreprocesses::set(self.txn, &self.context, &cache.0)` when absent, then *always* reloads it (`CachedPreprocesses::get`) and calls `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`. There is no `CachedPreprocesses::delete`, no binding of the cached entry to a specific message, and no binding to a specific signing set.
- `share_internal` (lines 150-181) calls `self.preprocess_internal(participants).0` and then `machine.sign(preprocesses, msg)`. Any second invocation of `share_internal` for the same `context` — with a different `msg`, or with a different set of submitted `serialized_preprocesses` — reuses identical `(d, e)` nonces.

Reachability: `msg` and `serialized_preprocesses` are protocol inputs. An unprivileged participant controls its own preprocess bytes and the message being routed for signing; coordinator code paths that re-attempt signing under the same context (retries, re-delivered signable items, distinct preprocess sets racing under one context) re-enter `share_internal` with attacker-influenced `preprocesses`/`msg`. Nothing in the code prevents a second `sign` with a different `msg` or different peer preprocesses under an identical context.

### Impact Explanation
FROST signature share: `s_i = d_i + e_i·ρ_i + λ_i·x_i·c`.

- If the same nonces are reused with the same signing set but a different message (so ρ_i identical, challenge `c` differs): `x_i = (s_i1 − s_i2) / (λ_i·(c1 − c2))` — direct recovery of the validator's secret key share `x_i` from two public shares.
- If peer preprocesses differ (so ρ_i differs): two shares give two linear equations; combined with the publicly broadcast shares of all signing-set members who reuse nonces, the system yields the group key — the classic FROST nonce-reuse key-recovery the `CachedPreprocess` docs explicitly warn about.

Result: threshold key compromise — the attacker can reconstruct secret shares / the MuSig-FROST group private key and forge arbitrary signatures for the validator set, i.e., full unauthorized signing.

### Likelihood Explanation
The hazard is baked in, not incidental: the seed is written to a durable DB and restored unconditionally on every `preprocess_internal`/`share_internal` call for a context, with no deletion after a successful `complete`. Safety rests entirely on an unstated external invariant that `context` is unique per (message, signing-set) pair and that `share_internal` is never retried under a reused context with different inputs — an invariant neither enforced nor documented at this layer. Peer-controlled preprocess bytes and signable messages are routine unprivileged inputs, and any retry/reorg/duplicate-delivery path under the same context converts determinism into reuse. Severity: High — key-share/group-key recovery from public protocol messages, matching the CVE's high-severity use-after-free class mapped to a consumed-one-time resource.

### Recommendation
- Delete the `CachedPreprocesses` entry (transactional delete in the same `txn`) once `sign` has consumed the machine, or mark the entry spent and refuse `from_cache` on a spent seed — enforce the "use once" contract at the storage layer rather than by convention.
- Bind the cached seed to the signing instance: store a hash of `msg` (and the sorted participant set) alongside the seed and abort `share_internal` if a second call under the same context presents a different bound value.
- Prefer per-`sign`-attempt context derivation (mix `context` with a monotonically consumed attempt counter or the message digest when seeding `ChaCha20Rng`), so even accidental reuse yields distinct nonces.
- Add a regression test asserting two `share_internal` calls under one context cannot produce a second `sign`.

### Proof of Concept
```
// coordinator/src/tributary/signing_protocol.rs (abridged actual code)
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
    let (machine, _) = AlgorithmMachine::new(algorithm.clone(), keys.clone()).preprocess(&mut OsRng);
    let mut cache = machine.cache();
    for b in 0 .. 32 { cache.0[b] ^= encryption_key_slice[b]; }
    CachedPreprocesses::set(self.txn, &self.context, &cache.0);   // persisted
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
// ...decrypt...
let (machine, preprocess) =
    AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached)); // no deletion
```
```
// crypto/frost/src/sign.rs — nonces are a pure function of the seed
let mut rng = ChaCha20Rng::from_seed(*seed.0);
let (nonces, commitments) =
    Commitments::new::<_>(&mut rng, params.keys.original_secret_share(), &params.algorithm.nonces());
```
Attack sketch: an unprivileged participant causes the coordinator to run `share_internal` twice under the same `context` — first with `(preprocesses_A, msg_1)`, then with `(preprocesses_B, msg_2)` (or the same preprocesses and a second signable message routed under the same context). Both calls produce shares `s_1`, `s_2` broadcast publicly with identical nonce commitments. With identical signing sets, `x_i = (s_1 − s_2) / (λ_i·(c_1 − c_2))` recovers the coordinator's secret share directly; with differing ρ_i across the participant set, the collected public shares from all nonce-reusing signers solve for the group private key. The precondition is solely that `share_internal` executes more than once per `context` — the code contains no guard, no deletion, and no message binding preventing it.

**Uncertainty note:** whether any concrete caller currently re-enters `share_internal`/`preprocess_internal` under an identical `context` with a different `msg` or preprocess set could not be fully verified (the `context` instantiation sites were outside the searched paths). The defect — persistent, undeleted, deterministic reuse of a value the API mandates be single-use — is confirmed in the cited code; exploitability depends on that caller-side invariant which the code neither enforces nor documents.