### Title
Panic (DoS) on point-at-infinity in `x()`/`x_only()` reachable via adversarial FROST preprocess forcing an identity nonce sum - ([File: networks/bitcoin/src/crypto.rs](networks/bitcoin/src/crypto.rs))

### Summary
The bug class is a denial of service on attacker-controlled input. In safe-Rust Serai, the analog is a reachable panic on untrusted bytes. `crypto.rs`'s helper `x()` calls `encoded.x().expect("point at infinity")` (networks/bitcoin/src/crypto.rs:15), and `x_only()` similarly `expect`s (line 22). These are invoked from `Hram::hram` (line 59) on the aggregate nonce `R` and group key `A` whenever `sign_share` / `verify_share` / `verify` run inside `Schnorr` (the BIP-340 `Algorithm` used by `TransactionMachine` in `networks/bitcoin/src/wallet/send.rs`). The code comments acknowledge the panic (lines 54, 78-83) but dismiss it as "negligible probability ... even with malicious participants present" — which is incorrect, because a malicious signer can *deterministically* force the aggregated nonce sum `R` to the point at infinity.

### Finding Description
In FROST-style signing, each participant submits a preprocess containing nonce commitments `[D_i, E_i]` deserialized via `read_preprocess` (send.rs:351-353 → `C::read_G`). `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only checks canonical encoding, so the identity element's canonical encoding is accepted. The aggregate nonce is `R = Σ (D_i + ρ_i·E_i)` where `ρ_i` is the binding factor. An attacker who submits their preprocess after observing the other participants' preprocesses can set `E_attacker = identity` (making `ρ_attacker·E_attacker = identity` regardless of the binding factor) and `D_attacker = -Σ_{j≠attacker}(D_j + ρ_j·E_j)`, driving `R` to the point at infinity with probability 1. When `Hram::hram` then computes `x(R)` (crypto.rs:65), `to_encoded_point` on infinity yields no x-coordinate and `expect` panics, aborting `sign_share`/`verify_share`/`verify` for every honest participant and the completer — crashing the signing process instead of returning a `FrostError`.

### Impact Explanation
An unprivileged signing participant (or anyone able to feed bytes to `read_preprocess`) deterministically causes a panic-based denial of service of the threshold-signing session — the direct analog of the CVE's UAF DoS: attacker-supplied input reaches code that cannot handle a degenerate value and crashes rather than erroring.

### Likelihood Explanation
Deterministic once the attacker can submit preprocess bytes for a Bitcoin `TransactionMachine` signing session; the adversary controls `D`/`E` encodings and observes peers' commitments before choosing their own (the preprocess phase is unordered/broadcast). Cost is one preprocess message.

### Recommendation
Reject identity/non-prime-order nonce commitments in `read_preprocess`/`process_addendum` (check `bool::from(point.is_identity())` and reject), and/or make `x()`/`x_only()`/`Hram::hram` return `io::Error`/`FrostError` instead of panicking on the point at infinity. Also remove the incorrect "negligible probability" justification — the identity outcome is adversarially constructible, not random.

### Proof of Concept
1. Construct a `SignableTransaction` and `TransactionMachine` (`send.rs:273`), have each honest participant run `preprocess` and publish `Vec<Preprocess>` commitments.
2. Attacker reads all honest preprocesses via `read_preprocess`, computes `T = Σ_j (D_j + ρ_j·E_j)` using the standard binding factors, then submits a preprocess whose encodings are `E = identity` (canonical k256 infinity encoding, accepted by `read_G`) and `D = -T`.
3. When honest signers call `TransactionSignMachine::sign` → `sig.sign` → `sign_share` → `Hram::hram(R=identity, A, sighash)`, `x(R)` hits `expect("point at infinity")` (crypto.rs:15) and the process panics instead of returning `FrostError`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** networks/bitcoin/src/crypto.rs (L13-23)
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

**File:** networks/bitcoin/src/wallet/send.rs (L351-353)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
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
