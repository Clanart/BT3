### Title
Cached FROST preprocess seeds are keyed only by attempt number, not by validator set (genesis), causing cross-tributary nonce reuse and validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` persists a deterministic FROST preprocess seed in `CachedPreprocesses`, keyed solely by `context`. For `DkgConfirmer`, `context` is `(b"DkgConfirmer", attempt)` (`signing_protocol.rs:275`). Every other piece of DKG-confirmation state in the coordinator is keyed by the tributary's `genesis` (e.g., `ConfirmationNonces::get(txn, genesis, attempt)`, `DkgKeyPair::set(txn, spec.genesis(), attempt, ...)`, `DataDb::get(self.txn, genesis, ...)` in `handle.rs`), because the coordinator DB is shared across all tributary specs. `CachedPreprocesses` is the lone exception. Two distinct validator sets (different genesis/session) executing a DKG confirmation at the same `attempt` index therefore load the same cached seed, regenerate identical nonces, and publish signature shares over different messages — enabling recovery of the validator's Ristretto private key from public data.

### Finding Description
- `CachedPreprocesses` is declared as `CachedPreprocesses: (context: &impl Encode) -> [u8; 32]` (`signing_protocol.rs:88`), keyed only by `context`.
- `DkgConfirmer::signing_protocol` sets `context = (b"DkgConfirmer", self.attempt)` (`signing_protocol.rs:275`). `attempt` is a `u32` local to each tributary's DKG, starting at 0 for every spec.
- On first use, a fresh `CachedPreprocess` seed is generated and XOR-encrypted with `Blake2s256("Cached Preprocess Encryption Key" || context || key)` (`signing_protocol.rs:107-134`). Because the encryption key is also bound only to `context` and the (same) validator key, the seed stored under `("DkgConfirmer", 0)` by set A decrypts cleanly for set B.
- `AlgorithmSignMachine::from_cache` regenerates nonces deterministically via `seeded_preprocess`: `ChaCha20Rng::from_seed(*seed.0)` feeds `Commitments::new(&mut rng, params.keys.original_secret_share(), ...)` (`crypto/frost/src/sign.rs:127-133`). The MuSig secret share passed in is the same validator key (`musig(musig_context(...), self.key.clone(), participants)` → `original_secret_share` is unchanged by aggregation), so the nonce pair `(d, e)` is byte-identical across the two specs.
- `share_internal` then calls `machine.sign(preprocesses, msg)` (`signing_protocol.rs:170`) where `msg = set_keys_message(&self.spec.set(), removed, key_pair)` (`signing_protocol.rs:296-300`), which differs between specs (different set/session and key pair), while the nonce `k_i = d_i + ρ_i·e_i` reuses identical `d_i, e_i`.
- The resulting `SignatureShare` is published on the public tributary in `Transaction::DkgConfirmed { confirmation_share }` (`handle.rs:508-516`), and the completed MuSig signature is published on-chain via `publish_set_keys` (`handle.rs:551-559`).

This mirrors the report's bug class — a resource (here the cached preprocess seed) is authorized/looked up under an insufficiently scoped identifier and silently reused across distinct "projects" (validator sets), analogous to accessing a workflow instance belonging to a project the caller lacks permission for.

### Impact Explanation
The FROST share is `s_i = d_i + ρ_i·e_i + λ_i·c·x_i` (with the Schnorrkel challenge `c` over `R`, message, and group key). When the same `(d_i, e_i)` is used for two confirmations by the same validator at the same MuSig index but different `msg`/participant-sets, an observer obtains two shares:

- `s_A = d + ρ_A·e + λ_A·c_A·x`
- `s_B = d + ρ_B·e + λ_B·c_B·x`

`ρ_A, ρ_B, λ_A, λ_B, c_A, c_B` are all publicly computable from the published preprocesses, the group key, and the messages (`rho_transcript` binds `group_key`, `hash_msg(msg)`, and the commitments challenge — `sign.rs:361-379`). Two linear equations in the two unknowns `d + ρ·e` terms and `x` — more precisely, the nonce relation gives `k_A = k_B` only if `ρ` matched, but since `d, e` are the same scalars, the two share equations form two equations in `d, e, x` reduced via the public binding factors; standard FROST nonce-reuse algebra solves directly for `x`. The recovered `x` is the validator's long-lived Ristretto secret key — the root-of-trust key that signs `set_keys` confirmations and all validator-authenticated tributary transactions. Compromise of it lets an attacker forge that validator's attestations and DKG confirmations. Per the rules this is concrete key (share) recovery, reachable entirely from public inputs (published preprocesses and shares).

### Likelihood Explanation
Reachability requires the same coordinator DB to service two tributary specs whose DKG reaches the same `attempt` value while a common validator participates. Serai operates one tributary per network/session; `attempt` resets to 0 for each spec's first DKG, so any validator present in two sets (e.g., across a session rotation, or multiple networks sharing the coordinator) triggers the collision deterministically on the very first confirmation — no adversarial action beyond observing public tributary data is needed. The code contains no genesis/set check before `CachedPreprocesses::get` (`signing_protocol.rs:123,137`), and the in-file safety argument (`signing_protocol.rs:25-54`) only reasons about distinct messages *within* one context, never considering context collision across specs. Likelihood is therefore bounded by deployment topology rather than attacker capability: High when a coordinator validates multiple sets, which is the normal operating mode.

### Recommendation
Bind the cache key and the encryption context to the validator set. Change the `DkgConfirmer` context from `(b"DkgConfirmer", self.attempt)` to `(b"DkgConfirmer", spec.genesis(), self.attempt)` — or equivalently pass `spec.set()`/`genesis` into `SigningProtocol.context` — so `CachedPreprocesses::get` can never return a seed minted under a different spec. As defense in depth, also mix `spec.genesis()` into `encryption_key_preimage` (`signing_protocol.rs:108-113`) so a wrongly-keyed cache entry fails decryption rather than silently reusing nonces, and consider keying `CachedPreprocesses` by `(genesis, context)` at the `create_db!` table level to match every other table's scoping.

### Proof of Concept
```rust
// Same coordinator DB `txn`, same validator `key`.
// spec_A: tributary for session/set A (genesis GA)
// spec_B: tributary for session/set B (genesis GB), key participates in both.

// --- Set A, DKG attempt 0 ---
let mut conf_a = DkgConfirmer::new(&key, &spec_a, &mut txn, 0).unwrap();
let preprocess_a = conf_a.preprocess();          // stores seed S under ("DkgConfirmer", 0)
let share_a = conf_a
  .share(preprocesses_a, &key_pair_a)            // msg = set_keys_message(set_A, ...)
  .unwrap();
// share_a is published: Transaction::DkgConfirmed { attempt: 0, confirmation_share: share_a }

// --- Set B, DKG attempt 0 (different genesis, different participants, different key_pair) ---
let mut conf_b = DkgConfirmer::new(&key, &spec_b, &mut txn, 0).unwrap();
let preprocess_b = conf_b.preprocess();
// BUG: CachedPreprocesses::get(txn, &("DkgConfirmer", 0)) returns S.
// Encryption key is Blake2s256("Cached Preprocess Encryption Key" || ("DkgConfirmer",0) || key)
// — identical — so S decrypts and from_cache regenerates the SAME (d, e).
assert_eq!(preprocess_a, preprocess_b);          // identical nonce commitments

let share_b = conf_b
  .share(preprocesses_b, &key_pair_b)            // different msg, same (d, e)
  .unwrap();

// Recovery (public data: share_a, share_b, preprocesses, both set_keys messages):
//   s_A = d + rho_A*e + lam_A*c_A*x ;  s_B = d + rho_B*e + lam_B*c_B*x
//   Solve the 2x2 linear system over the scalar field:
//   [s_A]   [1  rho_A] [d]   [lam_A*c_A]
//   [   ] = [       ] [ ] + [        ] * x   =>  subtract & solve for x
//   [s_B]   [1  rho_B] [e]   [lam_B*c_B]
// With known rho/lambda/c, x is recovered linearly; verify G*x == key.to_point().
```

Key citation chain: `CachedPreprocesses` table keyed by `context` (`coordinator/src/tributary/signing_protocol.rs:88`), context = `(b"DkgConfirmer", attempt)` with no genesis (`:275`), unconditional reuse via `get`/`from_cache` (`:123-145`), deterministic nonce derivation from the seed (`crypto/frost/src/sign.rs:127-141`), differing message `set_keys_message(set, removed, key_pair)` (`signing_protocol.rs:296-300`), and public share publication (`coordinator/src/tributary/handle.rs:508-516`).