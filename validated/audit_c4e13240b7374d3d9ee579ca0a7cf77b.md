### Title
Aggregate Schnorr verification accepts signatures for null/identity public keys, enabling forgery for any signer entry whose key is the identity - (File: crypto/schnorr/src/aggregate.rs)

### Summary
Analogous to the Arvados PAM flaw — where a credential that should be rejected (disabled account) is nonetheless accepted — `SchnorrAggregate::verify` accepts a mathematically "valid" signature for a signer whose public key is the group identity. An identity public key is a null credential: no private key exists, no signature should ever verify, yet the verification equation has no check rejecting it, and a forger can craft `Rs`/`s` that satisfy it anyway.

### Finding Description
`SchnorrAggregate::verify` builds a multiexp statement per signer:

```rust
// crypto/schnorr/src/aggregate.rs
let z = weight(&mut digest);
pairs.push((z, self.Rs[i]));
pairs.push((z * challenge, *key));
```

`weight` is derived only from the transcript of challenges, so it is fully computable by the prover once `R` is fixed. When a listed `key` is the group identity `O`, that signer's contribution collapses to `z·R_i`, leaving the equation `Σ z_i·R_i − s·G = 0`. The attacker, controlling that signer's `R_i`, picks `R_i = k·G` for a chosen `k`, computes `z_i` locally (it depends only on the challenges, which bind `R_i`), and contributes `s_i = z_i·k` to the aggregate `s`. The batch relation holds and `verify` returns `true` — a signature validates for a "key" with no secret.

Nothing in `read` or `verify` rejects identity points: `SchnorrAggregate::read` accepts any encoding `C::read_G` permits, and the group identity has a canonical, accepted encoding for Ristretto and secp256k1. `verify` likewise checks only `Rs.len() == keys_and_challenges.len()` and the multiexp result — never that keys or nonces are non-identity. This mirrors the vulnerability class directly: verification of the credential (the Schnorr equation) is performed while the check that the account/key is one that *should be allowed to authenticate* (non-null key, actually registered as a real signer) is skipped.

### Impact Explanation
Any protocol that consumes `SchnorrAggregate::verify` over a signer list admitting an identity key accepts a forged signature: the attacker produces a valid aggregate asserting that signer approved an arbitrary message, without any secret. This is a signature forgery reachable entirely with attacker-chosen bytes (`SchnorrAggregate::read`) and attacker-chosen `R`/`s`. The same gap exists in the underlying primitive: `SchnorrSignature::verify` (`crypto/schnorr/src/lib.rs`, `batch_statements`) also emits `R + c·A − s·G` with no identity rejection, so a lone `(R = kG, s = z·k … )` forgery applies there too whenever the verifier's supplied `public_key` is identity.

### Likelihood Explanation
Exploitation requires an identity point to appear in the verifier's signer/key list — i.e., a "null" or uninitialized key being treated as a valid signer (the direct analog of a disabled account still being in the auth backend). Wherever keys are loaded from storage/deserialization (`ThresholdKeys::read`, `read_G` paths) or defaults, an identity encoding can plausibly surface; the library places no defense-in-depth check against it. The forgery itself is deterministic: `z` is a public function of the public challenges, so no grinding or privileged position is needed — the attacker just computes `s = z·k` offline.

### Recommendation
In `SchnorrAggregate::verify` and `SchnorrSignature::batch_statements`/`verify`, reject identity public keys and identity `R` values (e.g., `bool::from(key.is_identity())` / `R.is_identity()` short-circuit to `false`), and have `SchnorrAggregate::read`/callers reject identity nonces. Defense-in-depth at the verifier removes the reliance on every caller excluding null keys, just as the Arvados fix moved the account-validity check into the auth path itself rather than trusting PAM's result.

### Proof of Concept
Conceptual (no code changes required to demonstrate):

1. Verifier calls `verify(dst, keys_and_challenges)` where some entry `(A_i = O, c_i)` uses the identity point as that signer's key, and challenges are computed canonically binding `R_i` (e.g., `challenge(genesis, key_bytes, R_bytes, msg)` as in `Validators::verify_aggregate`).
2. Attacker chooses scalar `k`, sets `R_i = k·G`, computes `c_i` per the verifier's challenge function, then replays the verifier's weight derivation: build `DigestTranscript::<C::H>::new(dst)`, `domain_separate(b"signatures")`, append each `challenge.to_repr()`, and call `weight()` to obtain `z_i`.
3. Attacker sets their share `s_i = z_i·k` and submits `SchnorrAggregate { Rs: [.., R_i], s: Σ s_j }`.
4. The multiexp evaluates `z_i·(k·G) + z_i·c_i·O + … − s·G = 0`, so `verify` returns `true` — a forged signature on an arbitrary `msg` attributed to a key nobody controls.