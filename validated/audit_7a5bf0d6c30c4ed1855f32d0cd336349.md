### Title
Cached FROST preprocess seed is reused across distinct signing sessions, leaking the validator's secret share via nonce reuse - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
`SigningProtocol::preprocess_internal` derives the FROST nonces deterministically from a `CachedPreprocess` seed stored in `CachedPreprocesses`, keyed solely by `self.context`. Once a seed is cached for a context, every subsequent signing under that same context calls `AlgorithmSignMachine::from_cache` with the identical 32-byte seed, regenerating the identical nonce pair (`Commitments::new` from `ChaCha20Rng::from_seed`). If the same context ever fronts two distinct `(participants, msg)` signing sessions — e.g., a rebuilt process, or a second DKG-confirmation/`set_keys_message` under an unchanged context — the validator signs two different messages with the same nonce, allowing any counterparty to recover the validator's Ristretto secret share and thereby the MuSig root-of-trust key share.

### Finding Description
The flow is:

1. `preprocess_internal` computes `CachedPreprocesses::get(self.txn, &self.context)`; on first use it generates a machine via `preprocess(&mut OsRng)`, XORs `machine.cache().0` with a key derived from the private key, and stores it. On every later call with the same context it reloads and reuses that same seed (`coordinator/src/tributary/signing_protocol.rs:123-145`).
2. `from_cache` → `seeded_preprocess` runs `ChaCha20Rng::from_seed(*seed.0)` and `Commitments::new(&mut rng, original_secret_share, nonces)`, so identical seed ⇒ identical nonces and identical published commitments (`crypto/frost/src/sign.rs:121-144`).
3. `share_internal` rebuilds the machine via `preprocess_internal` again and calls `machine.sign(preprocesses, msg)` (`coordinator/src/tributary/signing_protocol.rs:150-180`). The resulting share is `s = d + e·ρ + c·λ·x_i` (Schnorr `sign_share`, `crypto/frost/src/algorithm.rs:201-211`). With fixed `d, e` (same nonce seed) and a different challenge `c` (different message or different counterparty preprocess set/binding factors), two observed shares yield `x_i = (s1 − s2)/(λ(c1 − c2))` — full secret-share recovery.

The file header acknowledges nonce reuse is unsafe and argues safety comes from "nonces being context-bound under a BFT protocol" plus a rebuild re-deciding nonces (`signing_protocol.rs:25-54`). Two holes break that argument:

- The cache is keyed by `context` alone, not by the session's `(commitments, msg)` tuple. Any second signing under the same context silently reuses the seed; nothing in `share_internal` checks that the published commitments correspond to this seed or this message.
- The code itself flags the missing guard: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)" (`signing_protocol.rs:50-54`). Without that check, any path that reaches `sign` with a distinct finalized message set (re-execution after partial state divergence, a second `set_keys_message` confirmation under a reused context value, or distinct co-preprocesses accepted in `share_internal`) produces a second share under the same nonce.

Analog to the report: just as `RedirectAgent` forwards `Authorization`/`Cookie` headers to an unintended cross-origin target, the cached preprocess forwards the secret-bearing nonce to an unintended signing context — the secret leaks to whoever observes both shares.

### Impact Explanation
Recovery of a validator's `ThresholdKeys<Ristretto>` secret share under the MuSig aggregation that is the "root of trust" (`signing_protocol.rs:2-8`). With enough recovered shares an attacker can forge the on-chain DKG-confirmation signatures (`set_keys_message`), and a single leaked share weakens the threshold. Impact: key share recovery — a Critical-class outcome under the scan rules.

### Likelihood Explanation
Requires two `sign` calls under one `context` with differing `(preprocesses, msg)`. Reachable by an unprivileged participant whenever the coordinator re-executes `share_internal` against a different finalized preprocess set, or when a subsequent confirmation signing reuses the same context encoding — the DB schema (`CachedPreprocesses: (context) -> [u8; 32]`) provides no per-session uniqueness, and the protective commitment-consistency check is explicitly a TODO. The attacker needs only to observe the two published 32-byte shares and know both messages, all of which are public transcript data.

### Recommendation
- Key `CachedPreprocesses` by `(context, hash_of_signing_session)` or refuse to `sign` twice from one seed.
- Implement the noted TODO: before publishing a share, verify the on-chain/finalized preprocess set matches the commitments derived from the cached seed; abort otherwise.
- Delete the cached seed from the DB after first use and treat any cache hit during `share_internal` that produces commitments differing from the broadcast ones as fatal.

### Proof of Concept
```rust
// Sketch using the in-scope frost API (crypto/frost)
let (machine1, pp) = AlgorithmSignMachine::from_cache(alg.clone(), keys.clone(), seed.clone());
let (m1, s1) = machine1.sign(preprocesses_a.clone(), msg_a).unwrap();

// Same context ⇒ same CachedPreprocess seed ⇒ identical nonces
let (machine2, _) = AlgorithmSignMachine::from_cache(alg, keys, seed);
let (m2, s2) = machine2.sign(preprocesses_b, msg_b).unwrap();

// Schnorr share: s = k + c * lambda * x_i  (same k, differing c)
// x_i = (s1.s - s2.s) / (lambda * (c1 - c2))
```
Concretely in `coordinator/src/tributary/signing_protocol.rs`: two invocations of `share_internal` under equal `self.context` with different `serialized_preprocesses`/`msg` cause `preprocess_internal` to hand both calls the same decrypted `cached` seed (lines 137-145), so both shares embed the identical nonce scalars while `Hram` challenges differ, enabling the linear solve above.