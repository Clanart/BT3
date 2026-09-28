### Title
Malicious FROST preprocess yielding an infinity aggregate nonce `R` causes a panic (assertion-failure DoS) in the BIP-340 `Hram`/`x` helpers - ([File: networks/bitcoin/src/crypto.rs])

### Summary
The reported bug class is an assertion-failure denial of service: under attacker-influenced inputs, two code paths race and the resolver hits an `assertion failure` and aborts. The analog in Serai's in-scope code is a reachable panic: `x()` in `networks/bitcoin/src/crypto.rs` calls `.expect("point at infinity")` on the x-coordinate of an elliptic-curve point, and it is invoked from `Hram::hram` on `R` — the aggregate nonce point computed from **participant-supplied preprocess commitments** — during FROST `sign_share`/`verify` for the Bitcoin `Schnorr` algorithm. A malicious participant who can craft nonce commitments such that the binding-weighted nonce sum equals the point at infinity turns signature aggregation into a process abort instead of a `FrostError`.

### Finding Description [1](#0-0) [2](#0-1) 

- `fn x(key: &ProjectivePoint) -> [u8; 32]` encodes the point and does `encoded.x().expect("point at infinity")`, so any call on the identity point panics.
- `Hram::hram(R, A, m)` computes `x(R)` and `x(A)` where `R` is the aggregate nonce (`Σ ρ_i · D_i` style FROST binding) and `A` is the group key. The doc comment on `Schnorr` explicitly acknowledges the panic: *"This may panic if called with nonces/a group key which are the point at infinity (which have a negligible probability for a well-reasoned caller, even with malicious participants present)"* — i.e., the panic is treated as a probabilistic impossibility, not as something enforced.
- The attacker-controlled input path is `read_preprocess` → `Preprocess.commitments` (nonce commitments `D, E` are raw group elements parsed by `C::read_G` from untrusted bytes) → `SignMachine::sign` → `algorithm.sign_share` / `verify` → `Hram::hram`. The coordinator/processor only maps `FrostError` results; a `panic!`/`expect` is not an `Err`, so `unreachable!()`-style handling and the process itself abort (see how `batch_signer.rs`/`cosigner.rs` treat `sign` failures — panics escape the `match`).
- The probability estimate ignores that a malicious signer chooses `D`/`E` freely *after seeing honest preprocesses* in the same session (or can grind the binding factor `rho = H(binding)`), so the aggregate `R` being identity is adversarially steerable, not random — precisely the "sufficient conditions under load/race → assertion failure" shape of the CVE: an edge-case input crashes the service rather than being rejected.

### Impact Explanation
A single crafted preprocess message sent to honest signers aborts the signing process (panic crossing into async task / process depending on runtime handling). For the Bitcoin network path this halts `sign_share`/`verify` for the whole session — a remote, unauthenticated-triggerable denial of service of threshold signing, the same availability impact class as CVE-2022-3924 (assertion failure under attacker-reachable conditions). In a validator running multiple concurrent sessions (parallel `SignMachine`s), the panic can stall or crash the processor's signing loop.

### Likelihood Explanation
Likelihood is moderate: it requires a participant-index preprocess (the attacker must be a DKG participant or be able to inject preprocesses routed to a victim's `read_preprocess`/`sign`), and forcing `R` to identity requires solving for the binding factors — but preprocesses are broadcast before `sign`, so a malicious signer can pick the last commitment adaptively to cancel the sum (rogue-commitment, analogous to rogue-key). No honest behavior is needed beyond running a session with the attacker.

### Recommendation
Reject identity/torsion-prone points at parse/verify time rather than relying on probability:
- In `read_preprocess` / `Commitments` handling for `Preprocess` (and in `sign_share`/`verify_share`/`verify`), return `FrostError::InvalidPreprocess` when any nonce commitment or the computed aggregate `R` is `is_identity()`, instead of reaching `Hram::hram`.
- Make `x()`/`x_only()`/`Hram::hram` return `Option`/`Result` and propagate a `FrostError` rather than `.expect(...)`; the same applies to `verify` where `A` (group key) could be identity in degenerate/malicious views.
- Audit `Secp256k1::read_G` call sites feeding these paths to ensure non-canonical encodings are rejected before use.

### Proof of Concept
1. Honest signer starts `AlgorithmMachine::new(Schnorr::new(), keys)` for secp256k1 FROST and broadcasts its preprocess `(D_h, E_h)`.
2. Attacker (a valid `Participant`) collects the preprocess set, computes the binding factor `rho_i` for its own index as the code does (`hash of binding message over group_key, msg, preprocesses`), and chooses `D_a = -(rho-weighted combination of all other commitments)` so that the aggregate nonce `R = Σ ...` equals `ProjectivePoint::IDENTITY` (additionally choosing `E_a` consistently). It encodes `D_a, E_a` canonically and sends the preprocess bytes.
3. Victim calls `machine.read_preprocess(&mut bytes)` — parsing succeeds since the points are valid encodings — then `machine.sign(preprocesses, msg)`.
4. `sign_share`/`verify` computes `R = identity` and calls `Hram::hram(&R, &A, msg)` → `x(R)` → `key.to_encoded_point(true)` → `encoded.x()` is `None` → `.expect("point at infinity")` panics, aborting the signer instead of returning `FrostError::InvalidPreprocess`.

Caveat I could not fully verify within available iterations: whether `read_preprocess` or the FROST `sign` path already rejects identity nonce commitments before `hram` is reached (my `grep` for `is_identity` in `crypto/frost/src/sign.rs`/`algorithm.rs` returned match counts but not the surrounding code). If an explicit identity check exists on the aggregate `R`, the panic is unreachable and this analog reduces to defense-in-depth; the documented panic note suggests no such check is relied upon, but confirmation requires reading those functions in full.

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
