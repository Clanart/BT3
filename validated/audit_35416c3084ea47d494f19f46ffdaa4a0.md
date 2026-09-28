### Title
BIP-340 Schnorr path does not normalize the group key parity, producing signatures that fail verification for odd-Y aggregate keys - (File: networks/bitcoin/src/crypto.rs)

### Summary
The BIP-340 algorithm adapter in `networks/bitcoin/src/crypto.rs` correctly handles the parity of the nonce point `R` (negating the challenge and final `s` when `R` has odd Y), but never normalizes the parity of the public key `A`. BIP-340 keys are x-only, so a signature must verify against `even_y(A)` — equivalently, the secret share must effectively be negated when the aggregate public key has an odd Y coordinate. The verify formula used is `s·G = R + c·A` against the un-negated `group_key`, which is the wrong equation for odd-Y keys.

### Finding Description
`Hram::hram` computes the BIP-340 challenge and conditionally negates it only based on `needs_negation(R)` (the nonce), handling `R`'s sign bit but not `A`'s: [1](#0-0) 

`Schnorr::verify` then delegates to `IetfSchnorr::verify`, which checks `SchnorrSignature { R, s }.verify(group_key, c)` — i.e., `s·G == R + c·group_key` using the group key exactly as it exists on the curve: [2](#0-1) [3](#0-2) 

`sign_share` similarly signs with `params.secret_share()` directly, with no conditional negation of the share when `group_key` is odd-Y: [4](#0-3) 

Meanwhile the funds are locked under the x-only encoding of that key (`x_only` drops the sign bit entirely): [5](#0-4) 

### Impact Explanation
When the aggregate FROST group key has odd Y (~50% of generated keys, before any TapTweak), a BIP-340 verifier computes `s·G ?= R + c·even_y(A) = R + c·(-A)`. Serai's own completion check verifies `s·G ?= R + c·A` against the un-negated key, so a "signature" accepted internally as `s = r + c·x` corresponds on-chain to `s·G = R + c·(-(-A))` — i.e., it only validates if `x` was signed as `-x`. Since the secret share is never parity-negated, half of all aggregate keys produce signatures that fail Bitcoin's BIP-340 validation, rendering outputs under those keys unspendable — funds reported received that are not spendable, and valid signing sessions producing signatures no conforming verifier accepts. This is exactly the CVE-2022-23001 class: an incorrect sign-bit/parity choice during point handling causing valid operations to produce output that fails downstream verification.

### Likelihood Explanation
Any processor validator set whose DKG/offset produces an odd-Y aggregate key hits this deterministically; an unprivileged party sending funds to such an address triggers the failure when the set attempts to spend. Reachability requires only public inputs (a Bitcoin deposit to the threshold address).

### Recommendation
In the Bitcoin `Schnorr` algorithm, apply `needs_negation(&group_key)` to both signing and verification: negate `params.secret_share()` (and each `verification_share`) when the group key is odd-Y, so all math is performed against `even_y(A)` matching the x-only key the funds are locked under. Add a test vector exercising an odd-Y aggregate key.

### Proof of Concept
1. Complete a `ThresholdKeys<Secp256k1>` keygen whose `group_key()` has odd Y (no parity correction exists in the shown code path).
2. Run `AlgorithmMachine::new(Schnorr::new(), keys)` through `preprocess`/`sign`/`complete` on any message.
3. `complete` succeeds: `SchnorrSignature::verify(group_key, c)` passes against the odd-Y `group_key`.
4. Serialize and submit to any BIP-340 verifier (e.g., `bitcoin::key::XOnlyPublicKey::verify` or an actual Taproot spend): the verifier uses `even_y(group_key)` and rejects the signature, since `s·G = R + c·A ≠ R + c·(-A)`.

Caveat: I was not able to inspect `networks/bitcoin/src/wallet/mod.rs` (which references `needs_negation`) and the DKG/offset registration path; if the key registration unconditionally applies a parity-normalizing offset (forcing the on-chain key to always correspond to the actual share's even form), this reduces to a non-issue. The in-scope signature/verifier code itself contains no key-parity normalization.

### Citations

**File:** networks/bitcoin/src/crypto.rs (L21-28)
```rust
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
}

/// Return if a point must be negated to have an even Y coordinate and be eligible for use.
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

**File:** crypto/frost/src/algorithm.rs (L208-211)
```rust
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
  }
```

**File:** crypto/frost/src/algorithm.rs (L214-217)
```rust
  fn verify(&self, group_key: C::G, nonces: &[Vec<C::G>], sum: C::F) -> Option<Self::Signature> {
    let sig = SchnorrSignature { R: nonces[0][0], s: sum };
    Some(sig).filter(|sig| sig.verify(group_key, self.c.unwrap()))
  }
```
