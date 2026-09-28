### Title
Reused cached FROST preprocess produces deterministic nonce reuse across signing sessions, enabling secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Surge report's root cause is that a security-critical value (utilization / borrow rate) is recomputed from attacker-influenceable *live* state instead of the state that was committed when the measurement period began — an attacker injects input in the same transaction to skew a retroactively-applied quantity. The Serai analog has the same structural inversion: a value that *must* be fresh per signing session (the FROST nonce seed) is instead read back from a persisted cache keyed only by context, so two distinct messages signed under the same context reuse identical nonces — the classic ROS/nonce-reuse condition that yields the signer's secret share. The caching exists in `SigningProtocol::preprocess_internal` in `coordinator/src/tributary/signing_protocol.rs`.

### Finding Description
`preprocess_internal` (signing_protocol.rs:100-148) derives an XOR encryption key from `"Cached Preprocess Encryption Key" || context.encode() || secret_key`, stores a `CachedPreprocess` (a 32-byte ChaCha20 seed) under `CachedPreprocesses::get(self.txn, &self.context)` if absent, and on every call rebuilds the signing machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`. `share_internal` (lines 150-181) calls `self.preprocess_internal(participants).0` each time a signature share is produced.

Inside FROST, `seeded_preprocess` (crypto/frost/src/sign.rs:121-144) instantiates `ChaCha20Rng::from_seed(*seed.0)` and derives both the nonces and their `Commitments` deterministically from that seed. The cache is only written when absent (`if CachedPreprocesses::get(...).is_none()` at line 123) and is never deleted or rotated after `share_internal` consumes it — there is no `CachedPreprocesses::del`/`kill` anywhere in the coordinator crate. Therefore every `share_internal` invocation under the same `context` produces byte-identical nonces and commitments while the `msg` (the serialized transaction data being signed) varies per attempt.

With two shares `s1 = d + rho*e + c1*share` and `s2 = d + rho*e + c2*share` over the same aggregated nonce commitment but different challenge `c`, any observer (all participants receive each other's shares; signatures become public on-chain) computes `share = (s1 - s2)/(c1 - c2)` — direct recovery of this validator's threshold private key share, and by extension (combined with the MuSig `Interpolation::Constant` binding factors of known form) contribution toward recovering the group key material.

### Impact Explanation
Secret share recovery. The FROST spec itself documents that reuse of a cached preprocess "will enable third-party recovery of your private key share" (crypto/frost/src/sign.rs:85-87 and spec/cryptography/FROST.md:51-55). Because the cache persists per-context rather than per-signing-attempt, any path that invokes `share_internal` twice for the same `context` with different `msg` produces two shares under one nonce — sufficient for unambiguous algebraic key-share extraction, not merely signature forgery.

### Likelihood Explanation
Reachable by an unprivileged party through public inputs. Signing sessions are triggered by on-chain transactions and validator-coordinated messages; an attacker does not need to be a validator to cause a signing attempt to be repeated (e.g., a signing round that fails to aggregate and is retried under the same context, or a second eventuality/Plan hashing to the same `context` encoding — `context` is only `Encode` and is not bound to the message). Since the encrypted cache is keyed solely by `context` and never cleared between attempts, the second attempt deterministically reuses the same nonce. The residual uncertainty is the exact coordinator-level condition that produces two distinct `msg` values under one `context`; this requires tracing `handle.rs`/`transaction.rs` call sites, which I could not fully verify, but nothing in `preprocess_internal` itself enforces single-use — the `MUST only be used once` contract documented on `CachedPreprocess` is not upheld by the code.

### Recommendation
Consume the cache: delete `CachedPreprocesses` for `context` immediately after `from_cache` succeeds (or at the start of `share_internal` before returning), so that any subsequent signing attempt under the same context generates a fresh preprocess via `machine.preprocess(&mut OsRng)` and re-caches it. Alternatively, key the cache by `(context, msg)` or by a monotonically-incrementing attempt counter so identical seeds can never span two messages — analogous to the report's fix of using a committed/stored value rather than a live, re-derivable one.

### Proof of Concept
1. Trigger a tributary signing session with context `C` and message `m1`; `share_internal` loads/ creates `CachedPreprocesses[C]` and emits share `s1` with commitment `R = D + rho*E` derived from `ChaCha20Rng::from_seed(seed)`.
2. Cause the same validator to sign `m2 != m1` under the same context `C` (failed aggregation followed by retry, or a second plan sharing the context encoding). `preprocess_internal` reads the still-present `CachedPreprocesses[C]` → identical nonces → same `R`, different challenge `c2`.
3. From the two public shares: `share = (s1 - s2) * inverse(c1 - c2)` — full recovery of the validator's threshold secret share on Ristretto.

```rust
// coordinator/src/tributary/signing_protocol.rs:123-147
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
  let (machine, _) = AlgorithmMachine::new(algorithm.clone(), keys.clone()).preprocess(&mut OsRng);
  let mut cache = machine.cache();
  for b in 0 .. 32 { cache.0[b] ^= encryption_key_slice[b]; }
  CachedPreprocesses::set(self.txn, &self.context, &cache.0);
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap(); // same seed every call
// ... no deletion; share_internal → machine.sign(preprocesses, msg) reuses identical nonces
```