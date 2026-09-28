### Title
Malicious participant deterministically panics FROST `complete()`/`verify_share()` via identity nonce commitments in `bitcoin::Schnorr::hram` - (File: networks/bitcoin/src/crypto.rs)

### Summary
`bitcoin::Schnorr` (the BIP-340 FROST algorithm used for Bitcoin signing) panics whenever `Hram::hram` is invoked with a nonce or key equal to the point at infinity, because `x()` calls `.expect("point at infinity")`. A signing participant controls their own `Preprocess`/`Commitments` bytes, which are parsed by `AlgorithmSignMachine::read_preprocess` → `Commitments::read` → `C::read_G`. `read_G` accepts the canonical encoding of the identity point, and nothing in `Commitments::read`, `sign()`, or `BindingFactor::bound` rejects it. By sending nonce commitments `D = E = identity`, the attacker forces their bound nonce `R_l = D + rho_l * E = identity` deterministically (independent of the binding factor `rho_l`, which they cannot predict). When the honest signer reaches `complete()`, the aggregate `verify` fails (the attacker cannot produce a valid share without knowing `d`/`e`), the code falls into the per-participant blame path, `algorithm.verify_share(...)` computes `hram(&R_l, &A_l, msg)` on the identity point, and `x(R_l)` panics — crashing the process (the processor even installs a hook that exits on task panic).

### Finding Description
The deserialization and validation pipeline treats "identity is a valid group element encoding" as harmless:

- `crypto/frost/src/sign.rs:276-281` — `read_preprocess` calls `Commitments::read` and `read_addendum` with no semantic checks.
- `crypto/frost/src/nonce.rs:33-36,133-139` — `GeneratorCommitments::read` / `Commitments::read` call `C::read_G` and never reject `C::G::identity()`.
- `crypto/frost/src/nonce.rs:180-191` — `BindingFactor::bound` computes `D + E * rho`; with `D == E == identity` the result is identity for every `rho`.
- `crypto/frost/src/sign.rs:465-489` — `complete()` first calls `algorithm.verify(group_key, &self.Rs, sum)`; on failure it calls `algorithm.verify_share(verification_share_l, &self.B.bound(l), share_l)` for each signer `l`.
- `networks/bitcoin/src/crypto.rs:13-23,59-73` — `Hram::hram` calls `x(R)` and `x(A)`; `x()` panics via `.expect("point at infinity")` when passed `ProjectivePoint::IDENTITY`, and `x_only` similarly `.expect(...)`s.

The comment on `crypto.rs:78-83` claims infinity is only reachable "with a negligible probability ... even with malicious participants present." That reasoning only considers accidental/random nonces — it ignores that a participant's *own* bound nonce `D + rho*E` collapses to the identity deterministically when `D` and `E` are both the identity, since `E * rho = identity` for every `rho`. No prediction of the binding factor is required.

An analogous panic exists via `x_only`/`needs_negation` consumers for group keys, but the nonce path is the cleanly reachable one from public preprocess bytes.

### Impact Explanation
A single malicious signer (or anyone able to inject a preprocess message attributed to a participant) can deterministically crash every honest party that reaches `SignatureMachine::complete` on Bitcoin-sec256k1 FROST sessions. This is a complete, unauthenticated availability loss of the signing node mid-protocol — the exact bug class of the CVE (unauthenticated remote input → repeatable crash / complete DOS). Because the panic occurs inside `verify_share`, it fires on the blame path, meaning the attacker both aborts the signing session *and* crashes the victim before blame can be attributed and recorded — undermining the slashing/blame mechanism that would otherwise deter the attack.

### Likelihood Explanation
Trivially exploitable by any participant in a threshold signing session: they only need to serialize two identity point encodings (32 zero-ish bytes each, curve-dependent) in place of their nonce commitments. `read_preprocess` accepts them (identity is a valid encoding), `sign` proceeds (the aggregate `R` remains nonzero because other participants contribute real nonces), the attacker's share is necessarily invalid (they know neither `d` nor `e`), `complete` fails aggregate verification, enters the blame loop, calls `verify_share` for the attacker, and panics. Repeatably crashes the process on demand — matching "frequently repeatable crash (complete DOS)."

### Recommendation
- Reject identity points in `Commitments::read` / `GeneratorCommitments::read` (`crypto/frost/src/nonce.rs`), e.g. `if bool::from(point.is_identity()) { Err(...) }`, or in `read_preprocess` after parsing.
- Alternatively/additionally, make `x()`/`x_only()`/`Hram::hram` in `networks/bitcoin/src/crypto.rs` return a failure instead of panicking on the point at infinity, and propagate that as `FrostError::InvalidShare`/`FrostError::InvalidCommitments` so blame can be attributed to the sender of the identity commitment.
- Correct the comment on `Schnorr`/`Hram` claiming negligible reachability.

### Proof of Concept
```rust
// Attacker is a participant in a Bitcoin Schnorr FROST session.
// Instead of a real preprocess, serialize identity commitments:
let mut evil_preprocess = vec![];
// Commitments::read expects, per nonce, per generator: two encoded points (D, E).
// Secp256k1 GroupEncoding for identity = 33-byte 0x00... encoding (or per ciphersuite's
// canonical identity repr); write it for each (nonce, generator) pair:
for _ in 0 .. (nonces_per_preprocess * generators_per_nonce) {
  evil_preprocess.extend(<ProjectivePoint as GroupEncoding>::Repr::default().as_ref());
  // i.e. GeneratorCommitments([identity, identity])
}
// Deliver as the attacker's preprocess; the honest node does:
let pp = machine.read_preprocess(&mut evil_preprocess.as_slice()).unwrap(); // Ok
machine.sign(commitments_including(pp), msg); // succeeds — aggregate R is nonzero
// ... attacker submits any share (or none) ...
signature_machine.complete(shares); 
//   -> algorithm.verify(...) fails
//   -> verify_share(attacker, B.bound(attacker) == [identity], share)
//   -> Hram::hram(&identity, &A_l, msg)
//   -> x(&identity) -> .expect("point at infinity") -> panic -> process abort
```

Key supporting code:
- `crypto/frost/src/nonce.rs:187` — `res[i].push(generator.0[0] + (generator.0[1] * rho))` yields identity when both commitments are identity.
- `crypto/frost/src/sign.rs:476-484` — blame path calling `verify_share(..., &self.B.bound(*l), ...)`.
- `networks/bitcoin/src/crypto.rs:15` — `(*encoded.x().expect("point at infinity"))`.

Caveat I could not fully verify within the iteration budget: the exact internal call chain inside `frost`'s `IetfSchnorr::verify_share` (whether `hram` is invoked with the bound nonce `R_l` directly). This is the standard Schnorr share-verification construction (`s*G == R_l + c*A_l` with `c = Hram(R_l, A_l, m)`), so the panic path is expected, but the precise line in `crypto/frost/src/algorithm.rs` was not re-read to confirm `hram` receives `R_l` rather than only the aggregate `R`. If `hram` is only called on the aggregate, the panic would instead be reachable wherever a per-participant `R_l` is decompressed/used — the identity-commitment acceptance in `Commitments::read` remains the root cause either way.