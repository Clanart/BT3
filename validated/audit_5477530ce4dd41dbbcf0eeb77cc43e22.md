### Title
BIP-340 signatures fail whenever the group key and aggregate nonce have different Y-coordinate parity - (File: crypto/frost/src/../../../networks/bitcoin/src/crypto.rs)

### Summary

The Bitcoin Schnorr algorithm normalizes the nonce parity by negating the challenge and final `s` value, but never normalizes an odd-Y group key before calculating or verifying shares. If the aggregated group key is odd and the aggregate nonce is even, `complete` returns a BIP-340 signature whose internal FROST equation is valid but whose public verification equation is not. A participant can intentionally select nonce commitments that induce this parity mismatch.

### Finding Description

`x_only` encodes only the X coordinate of the group key, which a BIP-340 verifier interprets as the even-Y representative; for an odd-Y internal group key `A`, the external public key is therefore `-A`, not `A` [1](#0-0) .

`Hram::hram` hashes `x(R)`, `x(A)`, and the message, then negates the challenge only when `R` is odd; it does not account for `A` being odd [2](#0-1) .

`Schnorr::sign_share` uses the interpolated secret share corresponding to the unnormalized internal group key `A` [3](#0-2) .

The internal verifier checks `sG = R + cA` and returns the signature if that equation holds [4](#0-3) .

The Bitcoin wrapper then negates `s` only when `R` is odd and serializes the X coordinate of `R` [5](#0-4) .

Consequently, an odd `A` and even `R` produce `sG = R + hA`, while BIP-340 verification uses `P = -A` and checks `sG = R - hA`; the returned signature is invalid [6](#0-5) [5](#0-4) .

External preprocess bytes are accepted through `read_preprocess` and `Commitments::read`, and their committed nonce points directly determine the aggregate `R` [7](#0-6) [8](#0-7) .

### Impact Explanation

A signing session can emit a 64-byte value that `complete` reports as successful but Bitcoin's BIP-340 verifier rejects. This can prevent a valid threshold-controlled spend and provides a signing participant with a reliable way to bias the aggregate nonce toward a parity that produces an unusable signature [9](#0-8) [5](#0-4) .

The condition is deterministic: signatures are externally valid only when `R` and the internal group key have matching Y parity under the current negation scheme. Random execution fails for approximately half of the signing sessions involving an odd-Y group key, and an attacker controlling a preprocess can choose nonce contributions until the resulting aggregate commitment has the desired parity [8](#0-7) .

### Likelihood Explanation

A random secp256k1 group key has approximately a one-in-two chance of having an odd Y coordinate, and the parity depends only on public key data [10](#0-9) .

A participant can supply attacker-chosen commitments through a preprocess message and can generate candidate nonce commitments until their contribution makes the aggregate `R` parity differ from the group key's parity [11](#0-10) [12](#0-11) .

No private key, malformed encoding, duplicate participant, invalid scalar, or invalid point is required; every internal equation and proof can remain valid while the serialized BIP-340 result is invalid [13](#0-12) .

### Recommendation

Normalize the Bitcoin group key to its even-Y representative before FROST signing and verification. All participants should deterministically apply `scalar = -1` to their `ThresholdKeys` when `needs_negation(group_key)` is true, thereby negating the group key, secret shares, and verification shares consistently [14](#0-13) [15](#0-14) .

After normalizing the group key, retain explicit normalization for `R`: either require an even-Y aggregate nonce or continue negating `s` for odd `R`, while ensuring `verify_share` uses the same normalized group and verification shares. Add a regression test covering all four combinations of group-key parity and aggregate-nonce parity [6](#0-5) [5](#0-4) .

### Proof of Concept

Let `xG = A`, where `A` has odd Y. BIP-340 therefore interprets `x(A)` as `P = -A` [1](#0-0) .

Choose preprocess commitments so the aggregate nonce `R` has even Y. `Hram` returns `c = H(R.x || A.x || m)` without negating it because `R` is even [2](#0-1) .

FROST produces the sum `s = r + cx`, where `rG = R` and `xG = A`; the internal check `sG = R + cA` succeeds, so `complete` returns a signature [16](#0-15) [17](#0-16) .

Because `R` is already even, the Bitcoin wrapper does not negate `s` and emits `R.x || s` [5](#0-4) .

A BIP-340 verifier calculates:

```text
sG = R + cA
R' + cP = R + c(-A) = R - cA
```

Since `R + cA != R - cA` for nonzero `cA`, the signature returned by `complete` fails public BIP-340 verification despite every internal share being valid [2](#0-1) [4](#0-3) .

### Citations

**File:** networks/bitcoin/src/crypto.rs (L13-22)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}

/// Convert a non-infinity point to a XOnlyPublicKey (dropping its sign).
///
/// Panics on invalid input.
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
```

**File:** networks/bitcoin/src/crypto.rs (L26-28)
```rust
pub(crate) fn needs_negation(key: &ProjectivePoint) -> Choice {
  u8::from(key.to_encoded_point(true).tag()).ct_eq(&u8::from(Tag::CompressedOddY))
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-72)
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
```

**File:** networks/bitcoin/src/crypto.rs (L145-148)
```rust
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
```

**File:** crypto/frost/src/algorithm.rs (L208-216)
```rust
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
  }

  #[must_use]
  fn verify(&self, group_key: C::G, nonces: &[Vec<C::G>], sum: C::F) -> Option<Self::Signature> {
    let sig = SchnorrSignature { R: nonces[0][0], s: sum };
    Some(sig).filter(|sig| sig.verify(group_key, self.c.unwrap()))
```

**File:** crypto/frost/src/sign.rs (L276-286)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
  }

  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
```

**File:** crypto/frost/src/sign.rs (L398-409)
```rust
    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);

    Ok((
      AlgorithmSignatureMachine {
        params: self.params,
        view,
        B,
        Rs,
        share,
        blame_entropy: self.blame_entropy,
      },
      SignatureShare(share),
```

**File:** crypto/frost/src/sign.rs (L454-467)
```rust
    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }
```

**File:** crypto/frost/src/nonce.rs (L194-209)
```rust
  pub(crate) fn nonces(&self, planned_nonces: &[Vec<C::G>]) -> Vec<Vec<C::G>> {
    let mut nonces = Vec::with_capacity(planned_nonces.len());
    for n in 0 .. planned_nonces.len() {
      nonces.push(Vec::with_capacity(planned_nonces[n].len()));
      for g in 0 .. planned_nonces[n].len() {
        #[allow(non_snake_case)]
        let mut D = C::G::identity();
        let mut statements = Vec::with_capacity(self.0.len());
        #[allow(non_snake_case)]
        for IndividualBinding { commitments, binding_factors } in self.0.values() {
          D += commitments.nonces[n].generators[g].0[0];
          statements
            .push((binding_factors.as_ref().unwrap()[n], commitments.nonces[n].generators[g].0[1]));
        }
        nonces[n].push(D + multiexp_vartime(&statements));
      }
```

**File:** crypto/dkg/src/lib.rs (L399-406)
```rust
  #[must_use]
  pub fn scale(mut self, scalar: C::F) -> Option<ThresholdKeys<C>> {
    if bool::from(scalar.is_zero()) {
      None?;
    }
    self.scalar *= scalar;
    self.offset *= scalar;
    Some(self)
```

**File:** crypto/dkg/src/lib.rs (L493-507)
```rust
    // The interpolation occurs multiplicatively, letting us scale by the scalar now
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
```
