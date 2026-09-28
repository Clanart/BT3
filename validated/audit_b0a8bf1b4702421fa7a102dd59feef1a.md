### Title
Cached FROST preprocess (nonce seed) is keyed only by attempt and is reused to sign different `set_keys_message` payloads — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The coordinator-side MuSig/FROST confirmer caches its preprocess seed under `CachedPreprocesses` keyed solely on `context = (b"DkgConfirmer", attempt)`. Every subsequent signing operation for that attempt — `DkgConfirmer::share` and `DkgConfirmer::complete` (which internally calls `share_internal` again) — rebuilds a sign machine from the *same* seed via `AlgorithmSignMachine::from_cache`. The message signed is `set_keys_message(set, removed, key_pair)` and the participant set is derived from `removed_as_of_dkg_attempt`, none of which are bound into the cache key. Any second call that resolves a different `key_pair` or `removed` set produces a second Schnorr share under the same nonce, allowing recovery of the validator's private key — the root-of-trust key used for all MuSig confirmation signatures.

### Finding Description
This is the Serai analogue of CVE-2014-0138: a pooled/reused context (the cached preprocess "connection") is reused even though the authenticating material (the message/signing-set "credentials") changed.

In `SigningProtocol::preprocess_internal`, the cache lookup is `CachedPreprocesses::get(self.txn, &self.context)` where `context = (b"DkgConfirmer", self.attempt)` (signing_protocol.rs:88, 123–147, 275). Nothing in the key commits to `key_pair`, `removed`, or the set of counterpart preprocesses. `share_internal` (lines 150–181) then calls `preprocess_internal` again and invokes `machine.sign(preprocesses, msg)`, where `msg = set_keys_message(&self.spec.set(), &removed, key_pair)` (lines 296–301).

Two reuse paths exist:

1. **Distinct `key_pair` under one attempt.** `generated_key_pair` (handle.rs:47–60) unconditionally executes `DkgKeyPair::set(txn, genesis, attempt, key_pair)` — overwriting any prior value — and then calls `.share(preprocesses, key_pair)`, signing whatever key pair the processor reports. There is no guard that this attempt already produced a confirmation share for a different `key_pair`. If a second `GeneratedKeyPair` report arrives for the same attempt (processor recomputing the DKG after additional shares/blame resolution, or a re-report after reboot), the same nonce signs a different message.

2. **Divergent commitment set / rebuilt process.** The file itself documents the missing safety check: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)" (lines 50–54). The `preprocesses` fed to `sign()` come from `ConfirmationNonces`, accumulated from on-chain `DkgShares` data (handle.rs:417, 526). If the accumulated preprocess set or `removed` set differs between the `share()` call (triggered by `generated_key_pair`) and the `complete()` call (triggered by `DkgConfirmed` accumulation), `sign()` is executed a second time with the same `d`/`e` nonces but different binding factors `rho_i` and a different challenge — the classic parallel-session/ROS-style share reuse the nonce-reuse warning in `crypto/frost/src/sign.rs:209–219` and `spec/cryptography/FROST.md:51–62` is designed to prevent.

Because `from_cache` deterministically re-derives `nonces` from the seed (`seeded_preprocess`, crypto/frost/src/sign.rs:121–144), the hiding/binding nonces are identical across both invocations. With two shares `s1 = k + λ1·e1·x`, `s2 = k + λ2·e2·x` over the same `k` but different challenges/bindings, the secret share `x` — here the validator's actual MuSig secret scalar — is recoverable by linear elimination.

### Impact Explanation
Both signature shares are published on the public Tributary chain (as `DkgConfirmed`/`confirmation_share` transactions), and the aggregate `R` is fixed by the nonce. Any observer who obtains two shares produced under the same cached seed with differing effective challenges can solve for the validator's secret key. That key authenticates the validator across the coordinator's root-of-trust MuSig (set-keys confirmations) — recovering it allows forging confirmation signatures and impersonating the validator in subsequent DKG confirmations. Per the project's own documentation, reuse of a preprocess "will enable third-party recovery of your private key share" (crypto/frost/src/sign.rs:85–87, 211–213).

### Likelihood Explanation
Medium. Triggering does not require breaking BFT consensus. `generated_key_pair` re-signs unconditionally on each report and overwrites `DkgKeyPair`, so any flow producing a second `GeneratedKeyPair` with a distinct pair for the same attempt suffices. Additionally, the author explicitly flags the unimplemented invariant (on-chain commitments matching the presumed cached preprocess) and acknowledges partial-rebuild scenarios where the persisted cache is reloaded against divergent on-chain data (lines 44–54). The window is narrow — it requires a same-attempt divergence of `key_pair`, `removed`, or the preprocess set between two sign executions — but no cryptographic barrier prevents it; only an unstated operational assumption does.

### Recommendation
- Bind the message and signing context into the cache key: derive `context` from `(b"DkgConfirmer", attempt, key_pair.encode(), removed.encode())`, or store the decided message alongside the seed and refuse to sign a different one.
- Implement the noted TODO: before producing a share, verify the preprocess commitments derived from the cached seed match the commitments this validator actually published on-chain for this attempt; abort (and rotate) otherwise.
- In `generated_key_pair`, reject a second `key_pair` for an attempt that already has `DkgKeyPair` set and a share produced.
- Track a `ShareEmitted` flag per context so `share()`/`complete()` cannot invoke `sign()` twice with differing inputs.

### Proof of Concept
Conceptual trace (no test harness in scope needed):

1. Attempt `a` begins; `dkg_confirmation_nonces` → `preprocess_internal` stores seed `S` under key `(b"DkgConfirmer", a)` and publishes preprocess commitments `C`.
2. Processor reports `GeneratedKeyPair(KP1)`: `generated_key_pair` sets `DkgKeyPair[a] = KP1`, calls `share(...)` → `from_cache(S)` → `sign(preprocesses, set_keys_message(set, removed, KP1))` → publishes share `s1`. Nonce `k` is fixed by `S`.
3. A second report `GeneratedKeyPair(KP2)` with `KP2 ≠ KP1` (e.g., processor recomputed the DKG result after processing blame, or a divergent on-chain preprocess set changed `rho`): `generated_key_pair` overwrites `DkgKeyPair[a] = KP2` and signs `set_keys_message(set, removed, KP2)` with the *same* `S` → share `s2` under identical nonce `k` but different challenge `e`.
4. Observer computes `x = (s1 − s2) / (λ·(e1 − e2))` (mod group order), recovering the validator's secret key.

The same structure applies to variant (2): `share()` executes with preprocess set `P1`/`removed1`, later `complete()` re-executes `share_internal` with `P2`/`removed2` (different `included` set → different `rho` binding factors per crypto/frost/src/sign.rs:361–379) → two shares, same `k`, different effective challenge → identical recovery.