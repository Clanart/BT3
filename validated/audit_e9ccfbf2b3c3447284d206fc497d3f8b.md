### Title
`ThresholdKeys` deserialization accepts identity/zero-valued verification shares yielding an identity group key with unspendable funds - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the "missing zero check" bug class (a zero-valued parameter silently renders the constructed object unusable and locks funds), `ThresholdKeys::read` and `ThresholdKeys::new` never validate that the deserialized `verification_shares` — or the interpolated `group_key` derived from them — are non-identity. An attacker who can supply `ThresholdKeys` bytes can therefore cause a victim to accept a group key equal to the identity point (or more generally a key with no known discrete logarithm). Any funds addressed to that group key (e.g., a Bitcoin Taproot output key derived via `musig`-style/scalar tweak of `group_key()`) are permanently unspendable, exactly matching the report's "contract unusable, funds locked" impact.

### Finding Description
`ThresholdKeys::new` (`crypto/dkg/src/lib.rs`, `ThresholdKeys::new`, ~lines 349–391) validates only the count and index range of `verification_shares` (`IncorrectAmountOfVerificationShares`, `InvalidParticipant`), then computes:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

There is no check that any `verification_shares` entry is non-identity, nor that the resulting `group_key` is non-identity. `ThresholdKeys::read` (~lines 574–632) reads each verification share with `<C as Ciphersuite>::read_G` — note this is the `Ciphersuite` point reader, not the identity-rejecting `Curve::read_G` used by FROST preprocess parsing (`crypto/frost/src/curve/mod.rs`, `Curve::read_G`, ~lines 123–131, which explicitly rejects identity). Similarly, `Interpolation::Constant` coefficients are read with `C::read_F` with no zero check, so a coefficient vector of all zeros is accepted and drives `group_key` to identity regardless of the shares.

Additionally, `secret_share` is read with `C::read_F` and accepted even when zero, producing a `ThresholdKeys` whose shares can never produce a valid FROST signature for the recorded `group_key` — again "usable-looking but permanently broken keys".

### Impact Explanation
Downstream code consumes `group_key()`/`original_group_key()` as the root of spendable outputs (e.g., Bitcoin key-path outputs in `networks/bitcoin` tweak this key; FROST signing in `crypto/frost` produces signatures bound to it). If `group_key` is identity, no scalar `d` exists with `dG = identity` besides `d = 0`-style triviality which produces no valid signature under the used verifiers, and key-path spending is impossible — funds sent to the derived address are locked forever, mirroring the reported exploit (a malformed/buggy input producing unusable state instead of reverting). Because `read` returns `Ok`, the corruption is silent and only surfaces after funds are deposited.

### Likelihood Explanation
Reachable wherever `ThresholdKeys::read` is fed bytes not fully trusted (the threat model explicitly includes untrusted bytes into `ThresholdKeys::read`), or where `ThresholdKeys::new` is called with attacker-influenced `verification_shares`/constant-interpolation coefficients — the constructor performs no semantic validation of share values, only participant indexes. It is analogous in difficulty to the original report: it requires a malformed input path (buggy/malicious serializer or sync peer) rather than cryptographic breakage.

### Recommendation
Reject identity points in verification shares and reject a zero `secret_share` and zero `Interpolation::Constant` coefficients inside `ThresholdKeys::new` (returning a `DkgError`), and/or have `read` use an identity-rejecting point reader as `Curve::read_G` does; assert `!group_key.is_identity()` before constructing `ThresholdCore`. Comprehensively validate all deserialized parameters rather than only participant indexes.

### Proof of Concept
```rust
// Feed ThresholdKeys::<C>::read a blob with t = n = 1, i = 1,
// Interpolation::Lagrange, secret_share = 0, and verification_shares[1]
// encoding the identity point. `Ciphersuite::read_G` accepts the identity
// encoding and `ThresholdKeys::new` performs no identity check, so read()
// returns Ok with keys.group_key() == C::G::identity().
//
// Alternatively t = n = 1 with Interpolation::Constant([C::F::ZERO]):
// group_key = verification_shares[1] * 0 = identity, still accepted.
//
// Any Bitcoin output paying to a taproot key derived from this group key
// is unspendable: no valid FROST signature can ever be produced and the
// key-path key has no usable discrete logarithm, permanently locking funds.
```

Relevant code:
- `crypto/dkg/src/lib.rs`: `ThresholdKeys::new` validates only count/indexes and sums `verification_shares[i] * interpolation_factor` into `group_key` without identity checks (~lines 349–391); `read` uses `C::read_G`/`C::read_F` without rejecting identity/zero (~lines 574–632).
- `crypto/frost/src/curve/mod.rs`: `Curve::read_G` (~lines 123–131) shows the intended identity-rejecting pattern that `ThresholdKeys::read` does not use.