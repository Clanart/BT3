### Title
Attacker-controlled FROST preprocess can force identity nonce-sum, panicking `x()`/`hram` and crashing the signer - (File: networks/bitcoin/src/crypto.rs)

### Summary
The referenced bug is improper validation of an attacker-supplied field's format, letting an unprivileged peer crash a component (CWE-1287, Medium). The Serai analog lives in the BIP-340 FROST algorithm: the nonce-sum `R` fed to `Hram::hram` is computed from participant-supplied preprocess commitments with no check that it is non-identity, and `hram` unconditionally calls `x(R)`, which `expect`s a non-infinity point. A malicious signing-set participant can choose `D`/`E` commitments that make the aggregated `R` the point at infinity, causing a panic inside `sign`/`sign_share` — an availability crash reachable purely by bytes sent over the authenticated preprocess channel.

### Finding Description
- `AlgorithmSignMachine::sign` computes per-signer binding factors and aggregated nonce points `Rs = B.nonces(&nonces)` from `preprocess.commitments` read via `read_preprocess` → `Commitments::read` → `GeneratorCommitments::read` → `C::read_G` [1](#0-0) [2](#0-1) 
- `read_G` only enforces canonicality; the identity/Infinity encoding is a canonical point and is accepted [3](#0-2) .
- `sign_share` then invokes `Hram::hram(&R_sum, &group_key, msg)` where `R_sum` is this aggregated nonce point. `hram` calls `x(R)`, documented "Panics on invalid input" — `encoded.x().expect("point at infinity")` [4](#0-3) [5](#0-4) .
- No validation in `sign`, `BindingFactor::nonces`, or `read_preprocess` rejects an identity `D`/`E` or an identity aggregate `R`. The Bitcoin `Schnorr` algorithm itself documents "This may panic if called with nonces/a group key which are the point at infinity", confirming the unchecked assumption [6](#0-5) .
- The same pattern exists in `SchnorrkelHram::hram`: `PublicKey::from_bytes(&point.to_bytes()).unwrap()` panics on an identity `R`/`A` (compressed identity is not a valid schnorrkel public key) [7](#0-6) .

An attacker who participates in the signing set sees all other preprocesses before `sign` executes (each participant's preprocess is broadcast, then `sign(commitments, msg)` consumes them). The attacker selects their own `NonceCommitments` values — e.g., `E = -D/rho` per generator once `rho` is computable, or simply picks commitments so their contribution cancels the honest aggregate — such that `R = Σ (D_l + rho_l·E_l)` for a nonce index is the point at infinity. When any honest signer runs `sign_share`, `x(&identity)` panics.

### Impact Explanation
Any FROST signing session over Secp256k1 (Bitcoin `Schnorr`) or Ristretto (`Schnorrkel`) can be crashed by one malicious participant supplying a crafted preprocess. The panic aborts the signer mid-protocol — a denial of service against threshold signing (and, in a node context, process termination), matching the report's availability-only impact class. Untrusted bytes reach `read_G`/`read_preprocess` and produce a panic rather than a handled `FrostError`.

### Likelihood Explanation
Requires only that the attacker be one of the `t` signing participants submitting a preprocess — an unprivileged message in the protocol's threat model (preprocesses are explicitly read from other parties via `read_preprocess`). No collusion, no BFT assumptions, no leaked keys. The exploit is deterministic once the other commitments are known.

### Recommendation
In `AlgorithmSignMachine::sign` (or `BindingFactor::nonces`), reject preprocess commitments equal to identity and reject any aggregated `R`/nonce-sum equal to identity with `FrostError::InvalidCommitments`/`InvalidSigningSet` before `sign_share` is invoked. Alternatively, make `x`/`x_only`/`Hram::hram` return `Option`/`Result` and propagate a `FrostError` instead of panicking. The same check applies to `SchnorrkelHram`'s `PublicKey::from_bytes(...).unwrap()`.

### Proof of Concept
1. Honest signer runs `AlgorithmMachine::new(Schnorr::new(), keys).preprocess(rng)` and broadcasts its preprocess.
2. Attacker waits for all other preprocesses, computes the honest aggregate `R_honest = Σ_{l≠attacker} (D_l + rho_l·E_l)` (rho is computable publicly from the `FROST_rho` transcript over group_key, `hash_msg(msg)`, and the preprocesses hash [8](#0-7) ).
3. Attacker serializes a `Preprocess` whose `GeneratorCommitments` for nonce index 0 satisfy `D_a + rho_a·E_a = -R_honest` (e.g., `rho_a = 0` ⇒ `D_a = -R_honest`, `E_a` arbitrary canonical encoding; identity encodings are accepted by `read_G`).
4. The honest signer calls `sign(...)` → `sign_share` → `Hram::hram(&R, ...)` with `R = identity` → `x(R)` hits `expect("point at infinity")` → panic/abort.

### Impact
Medium — deterministic, remotely triggerable crash of a FROST signing participant by a single malicious coparticipant via a malformed-but-canonical preprocess (CWE-1287 analog: unchecked assumption that a decoded field is a non-infinity point).

### Citations

**File:** crypto/frost/src/sign.rs (L361-379)
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
```

**File:** crypto/frost/src/sign.rs (L383-398)
```rust
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
```

**File:** crypto/frost/src/nonce.rs (L34-36)
```rust
  fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorCommitments<C>> {
    Ok(GeneratorCommitments([<C as Curve>::read_G(reader)?, <C as Curve>::read_G(reader)?]))
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
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
  }
```

**File:** networks/bitcoin/src/crypto.rs (L13-16)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-67)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);
```

**File:** networks/bitcoin/src/crypto.rs (L77-84)
```rust
  ///
  /// This may panic if called with nonces/a group key which are the point at infinity (which have
  /// a negligible probability for a well-reasoned caller, even with malicious participants
  /// present).
  ///
  /// `verify`, `verify_share` MUST be called after `sign_share` is called. Otherwise, this library
  /// MAY panic.
  #[derive(Clone)]
```

**File:** crypto/schnorrkel/src/lib.rs (L48-52)
```rust
    let convert =
      |point: &RistrettoPoint| PublicKey::from_bytes(&point.to_bytes()).unwrap().into_compressed();
    t.commit_point(b"sign:pk", &convert(A));
    t.commit_point(b"sign:R", &convert(R));
    Scalar::from_repr(t.challenge_scalar(b"sign:c").to_bytes()).unwrap()
```
