### Title
Cached FROST preprocess (nonce seed) is resumed under a context that does not bind the message/signing-set parameters, enabling nonce reuse across divergent signing sessions - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
CVE-2017-7468 is a "stale session resumption" bug: a TLS session was resumed under an old identity even though the client certificate had changed, because the resumption cache was not bound to the updated credential. The direct analog exists in `SigningProtocol::preprocess_internal` (`coordinator/src/tributary/signing_protocol.rs:100-148`): a FROST preprocess is deterministically regenerated from a cached 32-byte seed (`CachedPreprocesses`, keyed only by `self.context`), while the full session parameters — the MuSig `participants`/`keys` and ultimately the signed `msg` and preprocess set — are supplied fresh on each call and are *not* part of the cache key. The same nonce seed is therefore "resumed" even when the surrounding session parameters have changed.

### Finding Description
`preprocess_internal` loads (or creates) a deterministic nonce seed from the DB under `CachedPreprocesses::get(self.txn, &self.context)` and rebuilds the sign machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` (lines 123-145). The encryption key for the seed covers only `context || self.key` (lines 107-114); the `participants` list, the resulting `ThresholdKeys`, the signing set, and the message are unbound.

`DkgConfirmer` uses `context = (b"DkgConfirmer", self.attempt)` (line 275). Within a single attempt, three distinct entry points all rebuild a sign machine from the *same* cached seed:

- `DkgConfirmer::preprocess` (line 284)
- `DkgConfirmer::share` → `share_internal` → `preprocess_internal` + `machine.sign(preprocesses, msg)` (lines 288-310)
- `DkgConfirmer::complete` → `share_internal` again → `machine.sign(preprocesses, msg)` (lines 312-327)

`share` and `complete` each accept a caller-supplied `key_pair: &KeyPair` and `preprocesses: HashMap<Participant, Vec<u8>>`, and `msg` is derived at call time via `set_keys_message(set, removed, key_pair)` (lines 296-300). Consequently, the same FROST nonces `(d, e)` — regenerated deterministically from the resumed seed in `seeded_preprocess` (`crypto/frost/src/sign.rs:121-144`) — are signed over twice, once per invocation, with `msg`, the `included` set, and hence the binding factors `rho_i` and challenge `c` fully determined by the arguments of *that* call.

This mirrors CVE-2017-7468 exactly: the "session" (nonce seed) is resumed under a stale cache identity (`context`) that does not reflect the changed credential (the key pair being confirmed / the signing set). The `assert_eq!(&existing, preprocess)` guard in `FirstPreprocessDb` (`coordinator/src/db.rs:90-93`) shows the codebase knows distinct preprocesses under one identity are dangerous, but `CachedPreprocesses` performs no such consistency check on the parameters accompanying a resumed seed — `share` and `complete` are invoked with independently supplied `key_pair`/`preprocesses` under the same `context`.

### Impact Explanation
FROST signature shares are linear in the reused nonces: `s_j = d + e·ρ_j·λ_j + x_i·λ_j·c_j`, where `x_i` is the long-lived secret share of the MuSig key confirming validator-set keys. Each `sign()` invocation with the resumed seed fixes `(d, e)` while `ρ`, `c`, `λ` vary with the message and included set. Every additional `sign` under the same context yields another linear equation in the three unknowns `(d, e, x_i)`; three shares produced from the same seed under differing messages/signing sets suffice to solve for `x_i`, recovering the validator's key share for the set-confirmation MuSig key — the key that authorizes validator-set key rotations. Even two differing shares partially collapse the unknown space. This is precisely the failure the FROST docs warn about ("Reusing preprocesses would enable a third-party to recover your private key share", `spec/cryptography/FROST.md:51-53`), triggered not by the caller misusing `cache()` but by the coordinator's own resumption layer re-signing under a context that fails to bind the changed parameters.

### Likelihood Explanation
Every `share()` call followed by a `complete()` call for the same attempt deterministically performs two `sign()`s on identical nonces. Divergence requires only that the arguments differ between the two calls — e.g., a `complete` issued for a different `key_pair` or a different collected preprocess set under the same attempt number, which the API permits with no consistency check (the DB stores only the seed under `context`; nothing records which `msg`/`participants` it was resumed for). The inputs originate from coordinator/tributary message handling of externally contributed preprocess and share data, so the trigger path is on the network-facing handling path rather than a purely internal invariant. The residual question for severity is how freely the tributary layer permits differing `key_pair`/`preprocesses` per attempt; the code enforces nothing preventing it at this layer.

### Recommendation
Bind the resumed session to its full parameter set, as TLS should have bound the resumption to the certificate: store alongside `CachedPreprocesses[context]` a commitment (hash) of `participants`/`keys` and the first `msg`/preprocess-set digest observed for that context, and `assert` equality on every `share`/`complete` rebuild — aborting rather than signing if they differ. Alternatively, scope the cache key to `context || hash(participants, msg)` so a changed credential can never resume a stale seed, and consume (delete) the seed upon first share production so `complete` cannot silently re-sign.

### Proof of Concept
```
// Within coordinator/src/tributary/signing_protocol.rs semantics:
// attempt = k fixed for both calls.

// Call 1 (DkgConfirmer::share):
let ctx = (b"DkgConfirmer", k);
let (m1, _) = from_cache(alg, keys, seed(ctx));       // nonces (d, e) = f(seed)
let share1 = m1.sign(preprocesses_A, msg(key_pair_A)); // s1 = d + e·ρ1·λ + x·λ·c1

// Call 2 (DkgConfirmer::complete → share_internal):
let (m2, _) = from_cache(alg, keys, seed(ctx));        // SAME (d, e): cache hit on ctx
let share2 = m2.sign(preprocesses_B, msg(key_pair_B)); // s2 = d + e·ρ2·λ + x·λ·c2

// s1, s2 are broadcast/emitted values. (ρ1,c1,λ) and (ρ2,c2,λ) are
// publicly derivable from the preprocess sets and msgs. Two equations,
// unknowns (d, e, x); a third such call (another complete under attempt k)
// yields a determined system → x_i (validator's MuSig secret share) recovered.
```

The nonce commitments embedded in the emitted preprocess are identical across both calls (deterministic from the seed), making the reuse directly observable on-chain/tributary, and the differing `rho`/`c` are computable by any observer of the preprocesses and messages — no access to the DB or secret material required.