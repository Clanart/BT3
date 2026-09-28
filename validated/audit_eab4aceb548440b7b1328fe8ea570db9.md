### Title
Cached FROST preprocess seed is reused across repeated `share`/`complete` invocations under the same attempt, causing nonce reuse and validator key-share recovery - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
The external report describes a double-free: a resource that should have been consumed once is used again after an "early reset". The Serai analog is a **double-use of a one-time FROST preprocess (nonce seed)**: `SigningProtocol::preprocess_internal` persists a `CachedPreprocess` keyed only by context (e.g. `(b"DkgConfirmer", attempt)`), and every call to `preprocess()`, `share()`, or `complete()` regenerates the *identical* nonces from that seed and signs with them again. `DkgConfirmer::complete` itself calls `share_internal` a second time after `share` already produced a signature share. If any two signing invocations under the same context see different peer preprocesses or a different message, the same nonce scalar `d + ρ·e` is used with different binding factors/challenges, leaking the signer's secret share by linear algebra — the exact "reuse enables third-party recovery of your private key share" the code itself warns about.

### Finding Description
`preprocess_internal` (lines 100–148) derives `CachedPreprocess` nonces deterministically:

- If `CachedPreprocesses::get(txn, &context)` is empty, it creates a machine, caches the seed (XOR-obfuscated), and stores it (lines 123–134).
- It then loads the stored seed and calls `AlgorithmSignMachine::from_cache`, which routes to `seeded_preprocess`, deriving nonces via `ChaCha20Rng::from_seed(seed)` (crypto/frost/src/sign.rs:127–141, 268–274).

Two properties combine dangerously:

1. **The cache is never invalidated.** After `sign` consumes the nonces (`self.nonces.drain(..)`, sign.rs:388), the seed remains in `CachedPreprocesses`. Every subsequent call under the same context reconstructs the same `d, e` nonces.
2. **The context binds neither the signing set nor the message.** Context is just `(b"DkgConfirmer", attempt)` (line 275) or `(b"DkgRemovalConfirmer", ...)`. The peer `preprocesses` map and `msg` are passed per-call (`share_internal`, lines 150–181), and `preprocesses` are externally supplied bytes parsed via `read_preprocess` (lines 160–168).

`DkgConfirmer::share` (line 304) and `DkgConfirmer::complete` (lines 312–327) each independently call `share_internal` → `preprocess_internal` → `sign`. `complete` re-signs with the same cached nonces at line 322 (`expect("trying to complete a machine which failed to preprocess")` confirms a second `sign` runs). The header comment (lines 25–48) explicitly documents that nonce reuse is catastrophic and relies entirely on BFT ordering plus "upon any complete rebuild, we'd re-decide nonces" — but `DkgConfirmer` *deliberately* re-decides nothing: it reuses the persisted seed, and a "partial rebuild" (re-execution with the same DB but a re-keyed `key_pair` argument, or a different received-preprocess set) re-signs under the same nonce. The TODO at line 53 ("review how we're handling Processor preprocesses") acknowledges this gap is unreviewed.

Each signature share has the form `s_i = d + ρ_i·e + λ·x·c_i` where `ρ_i` (binding factor over the transcripted preprocesses, sign.rs:361–396) and `c_i` (signature challenge over `msg`/`Rs`) are public and computable by any observer, while `d`, `e`, `x` are the unknowns. Each distinct reuse adds one equation; three shares over the same `(d, e)` pair with distinct `ρ_i`/`c_i` suffice to solve for `x`, the validator's MuSig secret share (and `d`, `e` for blame/evidence).

### Impact Explanation
Recovery of a validator's root-of-trust MuSig/Ristretto key share (`self.key`, line 93). With enough co-signer shares an attacker forges DKG confirmations / `set_keys` messages, i.e., signing of an unintended message class that controls validator-set keys — full threshold-signing compromise from public message data alone.

### Likelihood Explanation
Preprocesses and shares are public inputs supplied by other validators and the coordinator's own message-derivation path (`set_keys_message`, line 296), which depends on `key_pair` — a call-time argument, not bound into the nonce context. Any path that calls `share` or `complete` more than once per attempt with differing inputs — e.g., a retried confirmation attempt where the observed key pair or finalized preprocess set differs, or `complete` being invoked after `share` with an inconsistent preprocess map — triggers the reuse. The code's own safety argument (lines 34–48) enumerates exactly these conditions ("distinct received messages… and sign is called again") as the failure mode, and admits the protection is an assumption rather than an enforced mechanism; no guard prevents `sign` from running twice on the same seed.

### Recommendation
- Mark the `CachedPreprocesses` entry consumed before returning from `share_internal` (delete or set a spent flag within the same DB transaction that records the emitted share), so any second `sign` under the same context fails rather than re-deriving nonces.
- Bind the nonce context to the signing instance, not just `(label, attempt)`: include the participant set hash and a commitment to the finalized preprocess/message set in the `context` key, or persist the signed `(preprocesses, msg)` hash and reject `sign` on mismatch (the TODO at lines 50–54).
- Alternatively, derive the seed as `H(secret || context || committed_inputs)` so any input divergence yields fresh nonces, making reuse impossible regardless of caller behavior.

### Proof of Concept
```rust
// Conceptual, against coordinator/src/tributary/signing_protocol.rs
// Same context (b"DkgConfirmer", attempt) -> same cached seed -> same (d, e)

let mut c = DkgConfirmer::new(&key, &spec, &mut txn, attempt).unwrap();

// Invocation 1: sign with preprocess set A, key pair K1
let share1 = c.share(preprocesses_A.clone(), &key_pair_1).unwrap();
// share1 = d + rho_A * e + lambda * x * c_1

// Invocation 2 (e.g., retried attempt sees set B or a different key pair)
let share2 = c.share(preprocesses_B.clone(), &key_pair_2).unwrap();
// share2 = d + rho_B * e + lambda * x * c_2   (same d, e!)

// Invocation 3 yields a third equation; rho_* and c_* are publicly
// recomputable from the published preprocesses/messages.
// Solve the linear system:
//   s_i = d + rho_i*e + lambda_i*x*c_i   for i in {1,2,3}
// => recovers x (the MuSig secret share), d, and e.
//
// Note: DkgConfirmer::complete already performs this second sign
// internally (share_internal at line 322) after `share` signed once —
// any divergence between the two calls' preprocesses leaks immediately.
```

The concrete reachability caveat: this requires `share`/`complete` to execute twice under one attempt with differing inputs. The code asserts this can't happen only under the full-BFT/complete-rebuild assumptions documented at lines 34–48, while the mechanism itself (persisted seed, never invalidated, per-call `sign`) performs no such enforcement — matching the double-free class where a consumed one-time resource is touched again after a partial reset.