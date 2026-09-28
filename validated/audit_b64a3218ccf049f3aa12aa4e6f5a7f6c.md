### Title
Malicious FROST preprocess with crafted nonce commitments forces identity `R`, panicking BIP-340 `Hram`/`x()` during transaction signing — ([File: networks/bitcoin/src/crypto.rs])

### Summary
The bug class in CVE-2025-46705 is a reachable assertion/`unreachable` abort on attacker-controlled input, yielding denial of service. Serai's analog is in `networks/bitcoin/src/crypto.rs`: the BIP-340 `Hram::hram` calls `x(R)`, which does `encoded.x().expect("point at infinity")` (`crypto.rs:13-16`), and the code documents that it "will panic" if `R` is the point at infinity (`crypto.rs:54`, `crypto.rs:78-80`). `R` is the aggregate nonce commitment computed from participant-supplied preprocesses, so an unprivileged signing participant who supplies a crafted preprocess can drive `R` to identity and panic every honest signer/coordinator that calls `sign_share` — aborting the Bitcoin transaction-signing session.

### Finding Description
- `TransactionSignMachine::sign` calls each per-input `AlgorithmSignMachine::sign` (`send.rs:383`), which computes `Rs = B.nonces(&nonces)` — the per-nonce sum over all `included` participants' `base + rho * actual` commitments (`sign.rs:383`, `nonce.rs` `BindingFactor::nonces`).
- The commitments come from `read_preprocess` → `Commitments::read` (`sign.rs:276-281`), which deserializes points via `C::read_G` without enforcing that the final aggregate is non-identity.
- `Schnorr::sign_share` calls `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` (`algorithm.rs:208`), which for `bitcoin-serai`'s `Hram` calls `x(R)` (`crypto.rs:65`) → `expect("point at infinity")` → panic.
- A participant included in the signing set chooses their nonce commitment points as the additive inverse of the (known/adaptively chosen) sum of the other participants' weighted commitments, making the combined `R = 0`. Because preprocesses are gathered before `sign` is invoked and the binding factors `rho` are deterministic functions of all preprocesses, an attacker who submits their preprocess after seeing the others' can compute the cancelling commitment.

### Impact Explanation
Panic (abort of the signing task/process) triggered entirely by attacker-supplied bytes read through `read_preprocess` and consumed in `sign`/`sign_share`. In the processor's Bitcoin signing flow this kills the FROST session for a transaction the attacker caused to be signed, denying service — directly analogous to the Lasso `g_assert_not_reached` DoS. Depending on how the processor hosts the machine, the panic can crash the signing thread/process, requiring restart and halting all pending Bitcoin withdrawals.

### Likelihood Explanation
Any party whose preprocess is included in `included` (a participant in the signing protocol, i.e., an unprivileged party feeding messages to `sign`) can attempt it. It requires solving for a cancelling commitment given known `rho` values — all public/deterministic — so it is feasible whenever the attacker can choose their preprocess after observing others'. No secret material, collusion, or validator privileges are needed beyond being one of the `t` signers for the transaction. The panic is acknowledged in the code's own doc comments (`crypto.rs:54`, `crypto.rs:78-83`), but it is reachable from untrusted preprocess bytes, which the API does not guard against.

### Recommendation
- In `Commitments::read` / `BindingFactor::nonces`, reject identity aggregate nonces: after computing `Rs`, return `FrostError` if any `R` `is_identity()`, instead of letting it reach `hram`.
- Make `x()`/`x_only()` return `Option`/`io::Result` and propagate an error through `Hram`/`sign_share` rather than panicking.
- Optionally reject identity nonce commitments at deserialization (`C::read_G` wrapper for commitments) to fail the preprocess early.

### Proof of Concept
Conceptual (Rust, `bitcoin-serai` FROST path):

```rust
// Honest participants i=1..=t each produce (machine, preprocess).
// Attacker is participant l_att, included in the signing set.
// 1. Collect all other participants' serialized Preprocess messages.
// 2. Compute the deterministic binding factors: rho transcript over
//    group_key || hash_msg(msg) || hash_commitments(preprocesses transcript)
//    (crypto/frost/src/sign.rs:362-371). Because rho depends on the attacker's
//    own preprocess, the attacker iterates: pick a nonce scalar r_att, form the
//    commitment, compute rho values, then set their *two* nonce commitments
//    (base D_att and binding E_att) such that
//        D_att + rho_att * E_att = -(sum over l != att of (D_l + rho_l * E_l))
//    e.g. fix E_att = G (rho_att known), set D_att = -(S_others) - rho_att * G.
// 3. Broadcast that preprocess. Every honest signer runs
//    AlgorithmSignMachine::sign -> Schnorr::sign_share ->
//    Hram::hram(&nonce_sums[0][0], ...) where nonce_sums[0][0] == identity ->
//    x(R) -> encoded.x().expect("point at infinity")  // crypto.rs:15
//    => panic / abort in each honest signer's process.
```

Cited anchors: panic site `networks/bitcoin/src/crypto.rs:13-16`; documented panic reachability `crypto.rs:54,78-83`; attacker-controlled bytes enter via `read_preprocess`/`Commitments::read` at `crypto/frost/src/sign.rs:276-281`; aggregate `R` at `sign.rs:383`; `hram` call at `crypto/frost/src/algorithm.rs:208`.

Note: reachability assumes `Commitments::read`/`C::read_G` for secp256k1 accepts the identity point or at least commitments whose weighted sum is identity (there is no aggregate-identity check anywhere in `sign`), and that the attacker can order/adapt their preprocess after seeing others' preprocesses within the signing session — both consistent with the preprocess-broadcast model documented in `crypto/frost/src/sign.rs`.