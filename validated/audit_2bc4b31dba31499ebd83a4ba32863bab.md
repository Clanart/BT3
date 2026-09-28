### Title
Reuse of single-use FROST nonces across distinct `set_keys_message`s enables validator secret-key recovery — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel bug class is a resource released by two independent teardown paths (`rpmsg_chrdev_eptdev_destroy` and `rpmsg_dev_remove`), producing a refcount underflow / use-after-free. The Serai analog lives in `SigningProtocol::preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:100-148): a `CachedPreprocess` — a single-use FROST nonce seed — is stored in `CachedPreprocesses` keyed only by `context = (b"DkgConfirmer", attempt)`, read back, handed to `AlgorithmSignMachine::from_cache`, and **never deleted** (coordinator/src/tributary/signing_protocol.rs:137-145). Both `DkgConfirmer::share` (line 304) and `DkgConfirmer::complete` (line 312) funnel into `share_internal` → `preprocess_internal` → `machine.sign(preprocesses, msg)` (line 170), each consuming the machine over the *same* deterministic nonces. If two invocations within one attempt sign different `msg`s — i.e., different `key_pair` values producing different `set_keys_message` outputs (lines 296-300) — the same validator nonce signs two different messages.

### Finding Description
`from_cache`'s contract in crypto/frost/src/sign.rs:216-224 states the preprocess "must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share." Here reuse is designed in: `CachedPreprocesses::set` (line 134) persists the seed under `(b"DkgConfirmer", self.attempt)` and `get` is called on every `preprocess_internal` without any `del`. The header comment (lines 25-48) argues safety comes from nonces being "context-bound under a BFT protocol" — but the context binds only `(domain, attempt)`, not the message. The message is `set_keys_message(&self.spec.set(), &removed, key_pair)` (lines 296-300), and `key_pair` is supplied by the caller from externally-provided `GeneratedKeyPair` data. Any path where `share()` is invoked for key_pair A and `share()`/`complete()` is re-executed for a distinct key_pair B under the same attempt reuses the identical nonce pair for two Schnorr signature shares over different challenges. Because `share_internal` reconstructs the machine from the same seed each call, signing is deterministic per (context, msg) — but not per attempt.

### Impact Explanation
Two published shares `s1 = k - e1*x`, `s2 = k - e2*x` over messages with distinct challenges `e1 ≠ e2` let any observer recover `x = (s1 - s2)/(e2 - e1)`, the validator's MuSig secret share (its Ristretto private key — the root of trust per the file's own header). Shares are broadcast publicly in the signing protocol, so an unprivileged observer of the transcript can perform the recovery. This is secret-key-share recovery — squarely within scope severity.

### Likelihood Explanation
Triggering requires `share`/`complete` to run twice within one `attempt` over differing `key_pair`s — e.g., re-execution after reboot (the design explicitly relies on re-execution, lines 16-23), or competing `GeneratedKeyPair` submissions reaching the confirmer in the same attempt. The code itself acknowledges the gap: the TODO at lines 50-54 admits the commitment-vs-on-chain consistency check and "the same on-chain-preprocess-matches-presumed-preprocess check" are unimplemented. I could not fully trace `handle.rs` call sites to confirm every trigger path; the reuse mechanism itself, however, is unconditional — deletion is simply absent.

### Recommendation
Bind the cached-preprocess context to the full message: key `CachedPreprocesses` by `(b"DkgConfirmer", attempt, msg_hash)` or by the `key_pair`/`set_keys_message`, so a distinct message derives a fresh nonce seed. Additionally `del` the cached seed once `share_internal` completes for a given context, and add the noted check that locally-derived commitments match the on-chain preprocess before publishing a share.

### Proof of Concept
```
// Attempt N, DkgConfirmer::share(preprocesses, key_pair_A)
//   -> preprocess_internal: seed S cached under ("DkgConfirmer", N)
//   -> sign(.., set_keys_message(set, removed, KA)) -> share sA = k - eA*x ; k fixed by S

// Same attempt N, re-executed share/complete with key_pair_B != key_pair_A
//   -> preprocess_internal: get returns same seed S (never deleted)
//   -> from_cache regenerates identical nonce k
//   -> sign(.., set_keys_message(set, removed, KB)) -> share sB = k - eB*x

// Observer computes: x = (sA - sB) / (eB - eA)  — validator private key recovered.
```
Root cause: `CachedPreprocesses` keyed by `(b"DkgConfirmer", attempt)` only and never consumed — coordinator/src/tributary/signing_protocol.rs:86-90, 123-145.