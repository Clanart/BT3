### Title
`ThresholdKeys::read`/`ThresholdKeys::new` accept identity verification shares, yielding a group key of zero (identity) that anyone can sign for - (File: crypto/dkg/src/lib.rs)

### Summary
The reported bug class is "a critical value may be the zero value and is used without a `!= 0` check". Serai's analog lives in `crypto/dkg/src/lib.rs`: `ThresholdKeys::read` deserializes `n` verification shares via `C::read_G`, and `ThresholdKeys::new` derives `group_key` as `sum(verification_shares[i] * interpolation_factor(i))` over participants `1..=t` — with no check that any share, or the resulting `group_key`, is non-identity. `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91`) only enforces canonical encoding, so the point at infinity deserializes fine. If all verification shares for `1..=t` are the identity, `group_key` is the identity, i.e. the group "secret key" is scalar `0`.

### Finding Description
- `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, the interpolation blob, the secret share, and then `for l in (1..=n) { verification_shares.insert(l, read_G(reader)?) }` — it never rejects identity points.
- `ThresholdKeys::new` (crypto/dkg/src/lib.rs:376-378) computes `group_key = t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()`. With `verification_shares[1..=t]` all identity, `group_key == G::identity()`, corresponding to group secret `0`. Since `threshold == 0`/`participant == 0` are already rejected (`ThresholdParams::new` line 166-178, `Participant::new` line 29-35) and `scale` rejects a zero scalar (line 400-403), the zero point in `verification_shares` is the reachable "zero address" analog.
- The secret share is additionally unconstrained: `C::read_F` accepts `0`, but the identity-share path needs no secret-share cooperation — the verifier-visible group key is forged regardless of the stored secret share.

### Impact Explanation
The group's public key becomes the identity point, whose discrete log is publicly known (scalar `0`). Any unprivileged party can produce valid Schnorr/FROST signatures that verify under this `group_key` (e.g., `s` arbitrary with `R = s*G` for challenge schemes, or trivially for the FROST `sign` equation since the share-side public term collapses). Downstream, an address/script derived from this group key in bitcoin-serai corresponds to no real threshold quorum: funds sent to it are spendable by anyone (forged signatures) or unspendable by the supposed validators — matching "concrete signing of an unintended message / funds reported received that are not spendable".

### Likelihood Explanation
`ThresholdKeys::read` is on the sanctioned untrusted-bytes surface (listed `read_*` API). Any flow that loads keys from serialized/network-supplied bytes rather than from an internally executed DKG accepts this input. It requires no collusion, no broken transport, and no misuse beyond feeding crafted bytes — the same reachability class as the `to == address(0)` report.

### Recommendation
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs), reject identity verification shares when iterating `verification_shares.keys()` (and/or reject `group_key.is_identity()` after the `1..=t` sum), mirroring the existing `scale()` zero-scalar rejection. Optionally also reject `secret_share == 0` on `read`, since a zero share is never produced by honest generation.

### Proof of Concept
1. Serialize a `ThresholdKeys` blob for `(t, n, i)` = `(2, 3, 1)` with `Interpolation::Lagrange`, an arbitrary nonzero secret share, and set the three `read_G` point encodings to the canonical identity encoding for the ciphersuite (e.g., all-zero compressed point where the suite encodes identity that way — `read_G` accepts it because it round-trips canonically).
2. `ThresholdKeys::read` succeeds; `ThresholdKeys::new` computes `group_key = identity * λ1 + identity * λ2 = identity`.
3. `keys.group_key()` returns `C::G::identity()`. Produce a Schnorr signature under public key `identity` (equivalent to secret `0`): pick `r`, set `R = r*G`, `s = r + c*0 = r` — verification `s*G == R + c*identity` holds for any `c`.
4. The forged signature verifies against this group key without any participant's cooperation; conversely no honest quorum can produce the "intended" key since none exists.

Uncertainty noted: whether identity encodings pass each concrete suite's `from_bytes` canonical round-trip was not verified per-curve (kp256 search returned no indexed result); for standard prime-group encodings the identity point has a canonical encoding and `read_G`'s only check is canonicality, so the reasoning holds wherever `C::G::identity().to_bytes()` is accepted by `from_bytes`.