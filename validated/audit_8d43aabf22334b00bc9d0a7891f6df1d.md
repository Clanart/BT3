### Title
Malicious participant crashes the Bitcoin FROST signing machine by forcing the aggregate nonce R to the point at infinity — ([File: networks/bitcoin/src/crypto.rs](networks/bitcoin/src/crypto.rs))

### Summary
CVE-2019-14940 is a crash-on-invalid-input availability bug: a remote party feeds malformed input to a target, causing a panic. The Serai analog lives in the BIP-340 HRAm used by the Bitcoin network's FROST algorithm. `x()` in `crypto.rs` calls `encoded.x().expect("point at infinity")`, and `Hram::hram` invokes `x(R)` on the aggregate nonce point `nonce_sums[0][0]`. A malicious co-signer can craft their preprocess nonce commitments so the bound aggregate nonce `R = Σ (D_i + rho_i·E_i)` equals the identity point. `read_G` accepts the (canonical) identity encoding, and `Commitments::read`/`B.nonces` perform no identity check on the resulting sum, so `sign_share` panics inside `AlgorithmSignMachine::sign`, crashing the honest node's signing execution. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
In `crypto/frost/src/algorithm.rs` `Schnorr::sign_share` (line 208) computes `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` where `nonce_sums` comes from `B.nonces(&nonces)` in `crypto/frost/src/sign.rs` line 383 — the sum over all participants of `D_l + rho_l * E_l`. `rho_l` (the binding factor) is a public scalar derived from `rho_transcript` covering all preprocesses (`sign.rs` lines 362–371). A malicious participant who submits their preprocess last knows every other participant's commitments and can compute their own `rho_l` for a candidate preprocess, then solve `E_l = rho_l^{-1} * (T − D_l)` to make the total `R` equal the identity (they need only publish the corresponding commitment points; they never need to know the underlying scalar). For secp256k1, the identity point has a canonical SEC1 encoding (`0x00`), which `Ciphersuite::read_G` accepts since `from_bytes` + canonical round-trip succeeds (`crypto/ciphersuite/src/lib.rs` lines 91–100). With `R` equal to identity, `x(R)` at `networks/bitcoin/src/crypto.rs` line 15 hits `.expect("point at infinity")` and panics inside the honest participant's `sign()` call. The code comments claim this has only "a negligible probability ... even with malicious participants present" (lines 78–80), which is false: a malicious participant can deterministically force `R = ∞`. Note this does not even require submitting an identity *commitment* — the attacker forces the *weighted sum* to identity using non-identity `D`/`E`, so any identity rejection on individual commitments would not mitigate it. [4](#0-3) [5](#0-4) 

### Impact Explanation
An unprivileged co-signer (any participant in a threshold signing session, reachable purely through public preprocess bytes fed to `read_preprocess`/`Commitments::read`/`read_G`) can deterministically crash an honest processor's signing state machine. This is a denial of service against the Bitcoin signing pipeline: the panic aborts `sign`, prevents the signature share from being produced, and — depending on panic handling in the processor runtime — can take down the signing task/process. This matches the CVE class (crash on attacker-controlled input, CVSS A:H) and maps to Medium severity: no secret leakage, but reliable remote-triggered crash.

### Likelihood Explanation
Any participant in a signing set can trigger it. The attacker controls their preprocess bytes end-to-end, can compute `rho_l` locally since it is a deterministic hash of all preprocesses plus `group_key`/`msg`, and needs only one modular inverse — trivial computation. Ordering requirements (submitting preprocess last) are satisfiable in any asynchronous preprocess-collection round. The panic fires on the victim's side during `sign()`, before any share validity check, so blame/abort logic in `complete` never runs.

### Recommendation
Reject identity aggregate nonces rather than panicking. In `crypto/frost/src/algorithm.rs` `sign_share` (and symmetric paths in `verify`/`verify_share` that consume `nonces[0][0]`), return a `FrostError`/early `None` when `nonce_sums[0][0].is_identity()` instead of letting `Hram::hram` panic. Alternatively/additionally, make `x()`/`x_only()` in `networks/bitcoin/src/crypto.rs` return `Option`/`io::Result` and propagate an error, and consider rejecting identity points in `Ciphersuite::read_G` or at `Commitments::read`. Document that `Algorithm::sign_share` must handle, not panic on, adversarially-chosen identity nonce sums.

### Proof of Concept
Sketch (executable via the existing test harness pattern in `crypto/frost/src/tests/vectors.rs`, using `unsafe_override_preprocess` to set a victim machine and `read_preprocess` to feed attacker bytes):

```rust
// setup: honest participant h, attacker a in included set; keys as usual
// 1. Collect all other preprocesses. For attacker's preprocess choose any D_a
//    (non-identity) and compute rho_a locally:
//      rho_transcript = FROST_rho(group_key || hash_msg(msg) || hash_commitments(...))
//      rho_a = binding factor for a over that transcript
// 2. Let S = sum over l != a of (D_l + rho_l * E_l)  // known from other preprocesses
//    Set  E_a = rho_a^{-1} * (-S - D_a)              // point multiplication by inverse scalar
//    Now R = S + D_a + rho_a * E_a = identity.
// 3. Serialize preprocess { D_a, E_a } and feed to victim via read_preprocess.
//    Commitments::read -> Secp256k1::read_G accepts both points (canonical, non-identity).
// 4. Victim calls sign(preprocesses, msg):
//      B.nonces -> Rs[0][0] = identity
//      sign_share -> Hram::hram(&identity, &group_key, msg)
//      -> x(&identity) -> to_encoded_point(true).x() == None
//      -> .expect("point at infinity") PANICS  // crypto.rs:15
```

Panic site: `networks/bitcoin/src/crypto.rs` line 15 (`expect("point at infinity")`), reached from `Hram::hram` line 65 via `crypto/frost/src/algorithm.rs` line 208. The same panic can be triggered in `verify_share`'s `batch_statements` path if `R` ends up identity during `complete`'s blame phase.

Caveat: I could not confirm within the available search budget whether `Commitments::read` in `crypto/frost/src/nonce.rs` rejects identity commitments per se — but that is immaterial, since the attack uses two non-identity commitments whose *bound sum* is identity.

### Citations

**File:** networks/bitcoin/src/crypto.rs (L13-16)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
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

**File:** crypto/frost/src/algorithm.rs (L201-211)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
  }
```

**File:** crypto/frost/src/sign.rs (L361-383)
```rust
      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
    }

    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
