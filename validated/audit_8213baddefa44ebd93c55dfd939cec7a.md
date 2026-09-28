### Title
BIP-340 signature algorithm never accounts for group-key Y-parity, producing invalid signatures for odd-Y aggregate keys - (File: networks/bitcoin/src/crypto.rs)

### Summary
The `Schnorr` algorithm in `networks/bitcoin/src/crypto.rs` implements BIP-340 signing on top of FROST, but only normalizes the nonce point `R` to even-Y. It never normalizes the group key `A`. Whenever the threshold group key has an odd Y coordinate (~50% of keys), every signature produced is invalid under BIP-340 verification, while Serai's internal `verify` still accepts it.

### Finding Description
BIP-340 defines verification against the x-only public key, i.e. the even-Y lift of `x(A)`. Equivalently, a valid signature must satisfy `s·G == R_even + e·A_even`, where `A_even = A` if `A` is even-Y and `-A` if `A` is odd-Y.

In `Hram::hram`, the challenge `e = H(BIP0340/challenge || x(R) || x(A) || m)` is computed, then conditionally negated only when `R` is odd-Y (`needs_negation(R)`). No parity adjustment is applied for `A` [1](#0-0) .

`sign_share` delegates to `FrostSchnorr::sign_share`, producing `s = r + c·a_share` against the raw (possibly odd-Y) secret share and group key [2](#0-1) . `verify` checks `s·G == R + c·A` using the projective (un-normalized) `group_key`, then negates `s` if `R` is odd and serializes the signature by dropping R's sign byte [3](#0-2) .

Working through the math: in all cases the emitted signature satisfies `s'·G = R_even + e·A`. BIP-340 requires `R_even + e·A_even`. When `A` is odd-Y, `A_even = -A`, so the on-chain check computes `e·(-A)` and fails. Nowhere in the signing path is the secret share, the challenge, or `A` itself negated for key parity — unlike standard BIP-340 threshold constructions, which negate `a` (or equivalently sign with `-c`) when `A` is odd. Because internal verification and `verify_share` both operate on the un-normalized key, the invalid signature completes the FROST protocol successfully and is only rejected by Bitcoin consensus.

### Impact Explanation
For any FROST key generation that yields an odd-Y group key — which occurs with probability ~1/2 and cannot be prevented by honest participants — every Bitcoin signature the threshold group produces is invalid under BIP-340. Taproot outputs locked to the x-only group key become unspendable: the coordinator reports a completed signature that the Bitcoin network rejects. This is a silent loss-of-spendability condition affecting funds, not just a liveness hiccup, since retrying signing with the same key cannot succeed.

### Likelihood Explanation
No adversary is required; the bug triggers deterministically whenever the aggregate key's Y coordinate is odd, i.e. roughly half of all key generations. Any session/signing attempt on such a key produces a signature that passes all internal verification (`SchnorrSignature::verify` against the projective group key and per-share `batch_statements`) yet fails on-chain.

### Recommendation
When the group key `A` is odd-Y, negate the effective secret: either sign with challenge `c` applied to `-a_share` (equivalently, use `A_even` as the verification key and negate each share's key contribution), or fold key parity into `Hram`/`sign_share` by computing `s` against `-a` when `needs_negation(A)`. `verify` and `verify_share` must then check against `A_even` consistently so internal verification matches consensus semantics.

### Proof of Concept
Conceptually, for `ThresholdKeys<Secp256k1>` with `group_key()` odd-Y:

1. Run a FROST signing session with `Schnorr::new()` for any message `m`.
2. Internally, `s·G = R + c·A` holds, so `Schnorr::verify` returns the 64-byte signature `sig[1..]` with `s` negated if `R` was odd.
3. Verify under BIP-340: compute `e = H(x(R) || x(A) || m)`, lift `x(A)` to even-Y `A_even = -A`, check `s·G == R_even + e·A_even`. Since the signature encodes `+e·A` against odd `A`, the equation fails.

Every valid execution of the protocol on an odd-Y key produces a signature rejected by `bitcoin::secp256k1::schnorr` / Bitcoin consensus. The defect is that `Hram::hram` handles `needs_negation(R)` but nothing ever handles `needs_negation(&group_key)` [4](#0-3) [5](#0-4) .

### Citations

**File:** networks/bitcoin/src/crypto.rs (L26-28)
```rust
pub(crate) fn needs_negation(key: &ProjectivePoint) -> Choice {
  u8::from(key.to_encoded_point(true).tag()).ct_eq(&u8::from(Tag::CompressedOddY))
}
```

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

**File:** networks/bitcoin/src/crypto.rs (L128-136)
```rust
    fn sign_share(
      &mut self,
      params: &ThresholdView<Secp256k1>,
      nonce_sums: &[Vec<<Secp256k1 as Ciphersuite>::G>],
      nonces: Vec<Zeroizing<<Secp256k1 as Ciphersuite>::F>>,
      msg: &[u8],
    ) -> <Secp256k1 as Ciphersuite>::F {
      self.0.sign_share(params, nonce_sums, nonces, msg)
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
