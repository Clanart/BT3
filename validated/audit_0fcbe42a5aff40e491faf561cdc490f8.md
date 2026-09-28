### Title
ThresholdKeys group key is computed from only t of n verification shares, letting inconsistent share sets silently yield disjoint "invariants" per signing set - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The MANTRA-DEX report describes a stableswap invariant which is supposed to bind *all* tokens in a pool, but is instead computed over only the ask/offer pair — so different pairs preserve different invariants and swaps behave as if disjoint pools exist. Serai has a direct analog: `ThresholdKeys::new` derives the group key (the "invariant" every signing set is supposed to preserve) via Lagrange interpolation over only participants `1..=t`, never checking that the remaining `t+1..=n` verification shares — or even the supplied secret share — are consistent with that polynomial. When `ThresholdKeys` is reconstructed from untrusted bytes via `ThresholdKeys::read`, an attacker can craft verification shares such that different signing subsets interpolate different group keys, while `group_key()` always reports the key derived from the `1..=t` subset.

### Finding Description
In `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:376-378`), the group key is computed as:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

Only the verification shares for participants `1..=t` are used — exactly like the stableswap code summing only `offer_pool` and `ask_pool` while `n_coins` is set correctly. No consistency check is performed on:

- shares `t+1..=n` (analogous to the ignored third token `C`), and
- the provided `secret_share`, which is never checked against `verification_shares[i]` (`G * secret_share == verification_shares[i]` is never verified in `new`).

The deserialization path `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads `t`, `n`, `i`, an interpolation variant, a secret share, and `n` verification shares straight from the reader via `C::read_F` / `read_G` and passes them to `ThresholdKeys::new` without any cross-consistency validation. These bytes are in scope as an untrusted input.

The consequence mirrors the report: the preserved "invariant" (group key) differs per signing set. In `ThresholdKeys::view` (`crypto/dkg/src/lib.rs:463-533`), each participant's verification share is interpolated against the *included* set, and `sign_share`/`verify_share` in `crypto/frost/src/algorithm.rs:219-230` check each share individually via `batch_statements`. If the shares do not lie on one degree-`(t-1)` polynomial, a signing set that is not `1..=t` will interpolate a secret for a *different* polynomial — every individual share verifies correctly, yet the aggregated signature does not verify under `group_key()`.

### Impact Explanation
An attacker who can supply crafted `ThresholdKeys` bytes (e.g., a maliciously constructed key file, backup, or any channel where serialized keys are re-read) can create a key whose `group_key()` (used as the deposit/receive address for e.g. a Bitcoin multisig wallet) is attacker-controlled via the `1..=t` shares, while honest signing sets of size `t` that don't coincide with `1..=t` reconstruct a different secret and produce signatures that fail aggregate verification with *no attributable blame* — `verify_share` passes for each share because each is checked against its own (inconsistent) verification share. Result: funds sent to the reported group key are unspendable by the honest participants (DoS / fund lock, analogous to MANTRA's "different invariant preserved per pair"), or alternatively a key can be constructed where the stored `secret_share` doesn't match any verification share, making `i`'s share silently useless.

### Likelihood Explanation
Exploitation requires an attacker to influence the bytes passed to `ThresholdKeys::read` — this does not arise from the honest PedPoP/musig DKG paths (which generate consistent shares internally). It is a realistic threat only where serialized threshold keys cross a trust boundary (shared key material, restored backups, or key ceremonies assembled from per-participant transcripts). Given that constraint on reachability, this rates Medium.

### Recommendation
In `ThresholdKeys::new`, validate consistency of the full share set:

1. Verify `verification_shares[params.i()] == C::generator() * secret_share` — the caller's own share must match its verification share.
2. Verify that the verification shares form a single degree-`(t-1)` polynomial: for each participant `j` in `t+1..=n`, check `verification_shares[j] == Σ_{i ∈ 1..=t} λ_i(j) · verification_shares[i]` (or equivalently, verify the interpolation in `view` reproduces `group_key` for every subset — checking all `n` shares against the `1..=t` basis is sufficient and cheap).
3. For `Interpolation::Constant`, similarly verify `secret_share`'s contribution and that all coefficients/shares are consistent.

Alternatively, document that `read` input must be trusted and never accepted from an untrusted party — but explicit consistency checks are strictly safer.

### Proof of Concept
Conceptually, for `t = 2, n = 3`, `Interpolation::Lagrange`, over any ciphersuite `C`:

1. Pick two scalars `a, b` and set `V1 = G·a`, `V2 = G·b` (a degree-1 polynomial through indices 1, 2 defines group key `K0 = 2·V1 − V2`-style Lagrange combination).
2. Set `V3 = G·c` for `c ≠ f(3)` where `f` is the polynomial through `(1,a),(2,b)` — i.e., `c` inconsistent.
3. Serialize via the `ThresholdKeys::read` format: `t=2, n=3, i=3`, `Lagrange`, `secret_share = c`, shares `[V1, V2, V3]`.
4. `read` succeeds; `keys.group_key() == K0` (derived only from `V1, V2`).
5. Run FROST signing with `included = {2, 3}`: both shares individually pass `verify_share` against their verification shares, but `Σ λ_i · share_i` reconstructs `f'(0) ≠ K0`'s discrete log, so the aggregate Schnorr signature fails verification with no blame attributable — the exact "disjoint invariant per subset" behavior from the MANTRA report, now over signing sets instead of token pairs.