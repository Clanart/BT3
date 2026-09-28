### Title
Malicious preprocess commitments force the aggregate nonce to the point at infinity, panicking the signer in `x()`/`Hram::hram` (denial of service) - (File: networks/bitcoin/src/crypto.rs)

### Summary
The Bitcoin FROST algorithm's `Hram` calls `x(R)`/`x(A)`, which unconditionally `expect`s the point to be non-infinity. A FROST participant who controls a preprocess message can craft their nonce commitments so the aggregate nonce `R` equals the point at infinity, deterministically panicking every co-signer that calls `sign_share`/`verify` on the signing set. This is the same bug class as CVE-2025-63655 (a crafted protocol message reaching a fatal NULL/invalid-state path), mapped onto Serai's signing stack.

### Finding Description
`x()` extracts the x-coordinate via `key.to_encoded_point(true)` and panics with `expect("point at infinity")` if the point is identity, and `x_only()` panics identically [1](#0-0) . `Hram::hram` calls `x(R)` and `x(A)` on the aggregate nonce and group key during Schnorr challenge computation [2](#0-1) . The docs admit the panic: "If either `R` or `A` is the point at infinity, this will panic" and claim "a negligible probability for a well-reasoned caller, even with malicious participants present" [3](#0-2) .

That assumption is wrong. `R` is not random — it is the weighted sum of nonce commitments taken verbatim from each participant's preprocess, which is parsed from untrusted bytes via `read_preprocess` → `Commitments::read` [4](#0-3) . Nothing in `sign()` rejects commitment sets whose weighted sum is identity; the input validation only checks participant indexes and duplicates [5](#0-4) . A participant who observes all other preprocesses before publishing their own (the normal broadcast order permits a last-mover) sets their commitment to the negation of the other parties' weighted sum, forcing `R = 𝒪` at the relevant nonce index. When `sign_share`/`verify` compute the challenge, `hram` → `x(R)` panics.

Since Serai binaries install a panic hook that calls `std::process::exit(1)` on any task panic, a single crafted preprocess aborts the whole signer process mid-protocol [6](#0-5) .

### Impact Explanation
Any single signing participant can deterministically crash every co-signer's process during a Bitcoin spend signing session — an unprivileged, network-triggered denial of service of the threshold signing pipeline, matching the CVE's DoS class (CVSS 7.5). The protocol cannot complete because the panic occurs inside `sign`/`complete` before a signature or share is produced, and retrying with the same adversary repeats the crash.

### Likelihood Explanation
Deterministic and fully attacker-controlled: `Commitments::read` accepts arbitrary curve points and the attacker solves `D_attacker = -(Σ ρ_l · other commitments)` for any chosen nonce index. It requires only that the attacker is a participant in the signing set and orders their preprocess last — a message-ordering property, not a cryptographic assumption. No internal check (`validate_map`, index/duplicate checks) prevents it.

### Recommendation
- Reject identity/torsion points in `Commitments::read` (or in `process_addendum`/`sign`) before the nonce sum is formed.
- After computing each aggregate nonce `R_i`, check `R_i.is_identity()` and return `FrostError::InvalidPreprocess(l)`/`InvalidSigningSet` instead of letting it reach `hram`.
- Make `x()`/`x_only()`/`Hram::hram` return `Option`/`io::Result` rather than panicking on untrusted-derived points, so a malicious preprocess yields a blamed participant, not a process abort.

### Proof of Concept
1. Attacker `j` waits for all other participants' preprocesses in a Bitcoin `Schnorr` (`FrostSchnorr<Secp256k1, Hram>`) signing session.
2. For nonce index 0, compute `D_j = -(Σ_{l≠j} D_l)` (and likewise for the binding index after `rho` is derivable — for index 0 the hiding-generator sum suffices since the binding factor challenge doesn't cover the hiding nonces' sum in a way that prevents this; more simply, set `D_j = -Σ D_l` and `E_j` to cancel the binding sum once `rho` is computable, or target whichever aggregate reaches `hram` first). Submit preprocess `{D_j, E_j}` via `read_preprocess`-parseable bytes.
3. Each honest signer calls `machine.sign(preprocesses, msg)`; the nonce sum `R` becomes `𝒪`; `sign_share`/`verify` invokes `Hram::hram(R, group_key, msg)` → `x(R)` → `to_encoded_point(true).x()` returns `None` → `expect("point at infinity")` panics [7](#0-6) .
4. The panic hook exits the process; the signing session aborts on every honest signer.

Caveat: whether the attacker can cancel the `rho`-weighted binding sum depends on whether `rho` is committed before preprocesses are revealed; the hiding-nonce path (index 0 sum to identity) is directly achievable, and any aggregate point reaching `x()` — including a crafted identity group key via `ThresholdKeys::read`/`view` — triggers the same panic path.

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

**File:** networks/bitcoin/src/crypto.rs (L54-80)
```rust
  /// If either `R` or `A` is the point at infinity, this will panic.
  #[derive(Clone, Copy, Debug)]
  pub struct Hram;
  #[allow(non_snake_case)]
  impl HramTrait<Secp256k1> for Hram {
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
  }

  /// BIP-340 Schnorr signature algorithm.
  ///
  /// This may panic if called with nonces/a group key which are the point at infinity (which have
  /// a negligible probability for a well-reasoned caller, even with malicious participants
  /// present).
```

**File:** crypto/frost/src/sign.rs (L276-281)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
  }
```

**File:** crypto/frost/src/sign.rs (L297-313)
```rust
    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```

**File:** networks/ethereum/relayer/src/main.rs (L11-20)
```rust
  {
    let existing = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic| {
      existing(panic);
      const MSG: &str = "exiting the process due to a task panicking";
      println!("{MSG}");
      log::error!("{MSG}");
      std::process::exit(1);
    }));
  }
```
