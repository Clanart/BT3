### Title
Cached preprocess seed reused across every `share`/`complete` call in an attempt, causing deterministic FROST nonce reuse and secret-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` derives the FROST nonces deterministically from a `CachedPreprocess` seed stored in `CachedPreprocesses` keyed only by `context` (e.g. `(b"DkgConfirmer", attempt)`). The seed is created once and then *reused verbatim* on every subsequent invocation — `share()` and `complete()` each rebuild the sign machine from the same seed, and `share()` can be invoked multiple times per attempt with different `preprocesses`/`key_pair` inputs (different `msg`). This is the same TOCTOU shape as the reference report: a value fixed at "guard" time (the `is_none()` cache check) is silently reused at "use" time, but here the reused value is the signing nonce itself.

### Finding Description
- `preprocess_internal` writes `CachedPreprocesses` once per context, then on *every* call loads the same seed and runs `AlgorithmSignMachine::from_cache`, which calls `seeded_preprocess` → `Commitments::new` from `ChaCha20Rng::from_seed(*seed.0)` — identical nonces `(d, e)` every time (coordinator/src/tributary/signing_protocol.rs:123-147; crypto/frost/src/sign.rs:121-144).
- `DkgConfirmer::share` and `DkgConfirmer::complete` both call `share_internal`, which calls `preprocess_internal` again (lines 288-327). `share` is invoked per `key_pair`, and the message signed is `set_keys_message(set, removed, key_pair)` — different `key_pair` values under the same `attempt` produce **different messages signed with identical nonces**.
- With the same signing set, two shares satisfy `s1 = d + ρe + c1·λ·x` and `s2 = d + ρe + c2·λ·x`, so `x = (s1 − s2) / ((c1 − c2)·λ)`. With different preprocess sets (attacker-controlled subset of `serialized_preprocesses`), ρ and λ vary too, but a handful of parallel-session shares still yield a solvable linear system (ROS-style), since `d` and `e` are constant.
- The seed is never rotated or consumed: the `is_none()` check only guards initial creation, and nothing marks the cache as spent after a share is produced. The doc comments in `crypto/frost/src/sign.rs:209-219` explicitly state reuse of a `CachedPreprocess` enables recovery of the private key share.

### Impact Explanation
Any validator participating in a tributary DKG confirmation round can collect multiple Schnorrkel signature shares produced under the same cached seed (by submitting distinct `preprocesses` maps and/or distinct `key_pair` values, all reachable via `share`/`complete` during a single attempt). Recovering a victim validator's MuSig secret key lets the attacker forge that validator's participation in threshold signatures over validator-set keys — direct compromise of the signing protocol, qualifying as key share recovery / signing of unintended messages. Severity: High within the in-scope model.

### Likelihood Explanation
The trigger requires only that `share` (or `share` then `complete`) executes more than once for the same `(b"DkgConfirmer", attempt)` context with differing inputs — a normal occurrence since DKG confirmation signs `set_keys_message` per `KeyPair` and is driven by whatever preprocess map the peer supplies. No collusion threshold, malicious node, or leaked key is needed; the adversary just needs to be a validator able to submit preprocess/share messages. The only mitigating factor is whether higher-level code deduplicates share requests per context, which is not enforced in this file.

### Recommendation
Rotate or delete the cached seed after each `sign` call: derive the effective seed as `H(stored_seed || msg || serialized_preprocesses)` or store a per-context counter and mix it into `ChaCha20Rng::from_seed`, ensuring each distinct `(msg, signing set)` gets fresh nonces. Alternatively, mark `CachedPreprocesses[context]` consumed after first use and generate a fresh preprocess per `share` invocation.

### Proof of Concept
1. Victim validator runs `DkgConfirmer` for attempt `a`; `preprocess()` stores seed `S` under `("DkgConfirmer", a)`.
2. Attacker calls `share(preprocesses_A, key_pair_1)` on the victim → share `s1` over `msg1 = set_keys_message(..., key_pair_1)` with nonces `(d, e)` derived from `S`.
3. Attacker calls `share(preprocesses_A, key_pair_2)` → share `s2` over `msg2` with the *same* `(d, e)` and same binding factor ρ (same preprocess set).
4. Attacker computes `x = (s1 − s2) / ((c1 − c2)·λ_i)` where `c1, c2` are the publicly computable Schnorr challenges and `λ_i` the Lagrange coefficient — recovering the victim's secret share `x`.

```rust
// coordinator/src/tributary/signing_protocol.rs:123-147 — seed created once, reused every call
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
  let (machine, _) = AlgorithmMachine::new(algorithm.clone(), keys.clone()).preprocess(&mut OsRng);
  // ... encrypts and stores seed; never marked consumed
  CachedPreprocesses::set(self.txn, &self.context, &cache.0);
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap(); // same seed every share()
let (machine, preprocess) = AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

```rust
// crypto/frost/src/sign.rs:127-132 — nonces fully determined by the reused seed
let mut rng = ChaCha20Rng::from_seed(*seed.0);
let (nonces, commitments) = Commitments::new::<_>(&mut rng, ...);
```