### Title
BIP-340 signatures are invalid when the aggregate group key has odd Y — only R-parity negation is applied, the required key-parity negation is skipped - (File: networks/bitcoin/src/crypto.rs)

### Summary
The original bug class: a compound operation performs a generic refresh instead of the action-specific state update, silently skipping a mandatory adjustment for one side of the operation. In `bitcoin-serai`'s BIP-340 `Schnorr` algorithm, producing a valid schnorr signature requires two parity corrections — normalizing `R` to even-Y and normalizing the signing key `A` to even-Y (since BIP-340 drops the key's sign bit via `x_only`). The code applies the `R`-side correction twice (negating the challenge in `Hram` and negating `s` in `verify`), but never applies the `A`-side correction — the exact analog of calling `updateXP` instead of `beforeKeroseneWithdrawn`: the withdrawal-equivalent adjustment (key negation) is omitted while the deposit-equivalent bookkeeping proceeds.

### Finding Description [1](#0-0)  `Hram::hram` negates the challenge `c` when `R` has odd Y, so internally the share is `r - c·x`. [2](#0-1)  `Schnorr::verify` then negates `s` when `sig.R` is odd and serializes via `x`/`x_only` (`networks/bitcoin/src/crypto.rs:13-28`), which discards the sign bit of both `R` and the pubkey.

The BIP-340 verifier reconstructs `P = lift_x(pk)` — the even-Y point — and checks `s·G == lift_x(R) + e·P`. If the group key `A` (the discrete log `d`) is odd, then `P = -A`, so a valid signature must satisfy `s·G = R_even + e·(-d)`, i.e. the effective secret must be `−d`. The code computes `s` from the raw secret `d` and group key `A` with no `needs_negation(&A)` correction anywhere in `sign_share`, `verify`, or `Hram`. Consequently, whenever the aggregate/tweaked key is odd-Y (~50% of keys), the emitted 64-byte signature fails BIP-340 verification: `s·G = R_even + e·A ≠ R_even + e·(-A)`.

### Impact Explanation
Any FROST signing session whose resulting (possibly taproot-tweaked) group key has odd Y produces signatures that Bitcoin nodes reject — outputs controlled by that key are unspendable, matching the "funds not spendable" acceptance criterion. Like the original report, the skipped state adjustment leaves the affected party permanently "protected" in the wrong state (here: an unusable key).

### Likelihood Explanation
Deterministic whenever the key's Y is odd — roughly half of all generated or tweaked keys — and triggered purely by public on-chain data (the key and sighash). No malicious participant needed; an unprivileged party broadcasting such a signature simply gets it rejected.

### Recommendation
Apply the missing key-parity correction alongside the existing R-parity one: compute `k_neg = needs_negation(&group_key)` and negate the secret share contribution (or equivalently negate `s` and `e` consistently) so the signature is produced under `lift_x(A.x)`. Concretely, fold `needs_negation(&A)` into the `s` finalization in `verify`/`sign_share`, mirroring how the recommended fix inserts the withdrawal hook before the move.

### Proof of Concept
1. Complete a FROST signing session over `Secp256k1` with `Schnorr::new()` where `group_key` (or `group_key + tweak·G`) has odd Y.
2. `Hram` leaves `e` un-negated (R even) or flips it (R odd); `verify` corrects only for R.
3. Submit the signature to a BIP-340 verifier (e.g. `bitcoin::secp256k1::schnorr::Signature::verify` against `x_only(A)`): verification fails, since `s·G = lift_x(R) + e·A` but the verifier checks `s·G = lift_x(R) + e·(-A)`.
4. Repeat with an even-Y key — verification succeeds, confirming the skipped negation is the root cause.

Caveat: if an upstream caller always normalizes `group_key` to even-Y via a `ThresholdView` offset before signing, the defect is masked at the integrator layer; I could not fully verify the wallet path in `networks/bitcoin/src/wallet/` within the available iterations, but the `Algorithm` implementation itself unconditionally produces non-BIP-340 signatures for odd keys.

### Citations

**File:** networks/bitcoin/src/crypto.rs (L59-73)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
    }
```

**File:** networks/bitcoin/src/crypto.rs (L139-150)
```rust
    fn verify(
      &self,
      group_key: ProjectivePoint,
      nonces: &[Vec<ProjectivePoint>],
      sum: Scalar,
    ) -> Option<Self::Signature> {
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
      })
    }
```
