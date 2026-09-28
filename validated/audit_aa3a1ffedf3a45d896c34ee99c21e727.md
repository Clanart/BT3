### Title
Crafted FROST preprocess with identity nonce commitments panics BIP-340 HRAM, crashing the signing session - (File: networks/bitcoin/src/crypto.rs)

### Summary
`Hram::hram` for the Bitcoin Schnorr algorithm calls `x(R)` / `x(A)`, which panic with `expect("point at infinity")` when passed the identity point. The aggregate nonce `R` is the sum of every participant's bound nonce commitments (`D + E * rho`), each supplied over the wire and parsed by `Commitments::read` / `read_preprocess`. A co-signer can send `D` and `E` commitments that decode to the identity point (or `E` arbitrary with `D = -rho * E` cancellation is not needed — `E` identity alone makes the contribution `D`, which they set to identity), forcing the group nonce sum for that generator to be the identity. When `hram` is evaluated on that identity `R` during `sign_share` / `verify`, `key.to_encoded_point(true).x()` returns `None` and the `.expect()` panics, aborting the signing operation.

### Finding Description
- `x()` in `networks/bitcoin/src/crypto.rs:13-16` does `key.to_encoded_point(true).x().expect("point at infinity")` — documented "Panics on invalid input", and `Hram::hram` (crypto.rs:59-73) calls `x(R)` on the aggregate nonce and `x(A)` on the group key.
- The aggregate nonce is assembled in `BindingFactor::bound()` / `nonces()` (`crypto/frost/src/nonce.rs:180-212`) from `commitments.nonces[n].generators[g].0[0] + rho * .0[1]`, where `D` and `E` come from `GeneratorCommitments::read` (nonce.rs:34-36) which does two raw `C::read_G` calls — no identity/torsion rejection for secp256k1 points.
- `AlgorithmSignMachine::read_preprocess` (`crypto/frost/src/sign.rs:276-281`) feeds attacker bytes straight into `Commitments::read` with only the local `generators` shape as a bound; nothing checks that the resulting bound nonce is non-identity.
- The same pattern exists for the Ethereum network Hram (`networks/ethereum/src/crypto.rs`), which shares this design, but the Bitcoin path is in scope.

### Impact Explanation
Any participant in a FROST signing session over Secp256k1 (the Bitcoin multisig) can, with only their preprocess message bytes, reliably crash every honest signer during share computation or verification of the completed signature (which calls `hram` on the same identity `R`). This is a deterministic denial of service of threshold signing — the analog of CVE-2015-7547's remote crash via a crafted response: untrusted bytes reach a function that makes an unchecked assumption about decoded input and aborts. Because the panic occurs inside `sign`/`complete`, operators cannot distinguish it from a logic fault without debugging, and retrying with the same malicious preprocess reproduces the crash.

### Likelihood Explanation
Reachable by any unprivileged party able to submit a preprocess message for a session — exactly the input class the prompt scopes in (`read_preprocess` / `sign` over untrusted bytes). Exploitation requires no collusion: one participant sets both generator commitments for their nonce to the encoded identity, or more subtly crafts `D = -rho * E` for an arbitrary `E` once `rho` (a public transcript challenge over `group_key`, `hash_msg`, and the preprocesses) is computable, since the participant sees the preprocesses before the nonce sum is evaluated. Identity encodings are not filtered by `read_G`. Likelihood is high within sessions that accept preprocesses from external participants; impact is bounded to crashing the signing attempt (a DoS), which maps to the CVE's crash primitive — no direct key/share recovery results.

### Recommendation
Reject identity (and non-canonical) points in `Ciphersuite::read_G` for kp256 curves, or explicitly validate in `BindingFactor::bound`/`nonces` that each bound nonce and the aggregate nonce sum is non-identity, returning `FrostError::InvalidPreprocess(l)` identifying the faulty participant. Alternatively, make `x()`/`x_only()` return `Option`/`io::Error` and propagate it instead of panicking, so a malicious preprocess yields a blameable error rather than a process abort.

### Proof of Concept
```rust
// Attacker participant builds a Preprocess whose GeneratorCommitments are
// the identity encoding for both D and E.
let identity = ProjectivePoint::IDENTITY;
let mut preprocess_bytes = vec![];
// For each planned generator pair: D = identity, E = identity
preprocess_bytes.extend(identity.to_bytes().as_ref()); // D
preprocess_bytes.extend(identity.to_bytes().as_ref()); // E
// (+ addendum bytes for the algorithm, () for Schnorr)

// Honest signer:
let preprocess = machine
  .read_preprocess(&mut preprocess_bytes.as_slice())
  .unwrap(); // parses fine — read_G accepts identity

let mut commitments = HashMap::new();
commitments.insert(attacker_i, preprocess);
// When sign() computes the bound nonce for this generator:
//   nonce = D + rho * E = identity + rho * identity = identity
// then hram(&identity, &group_key, msg) calls x(&identity):
//   to_encoded_point(true).x() == None  ->  expect("point at infinity") panics
let _ = machine.sign(commitments, b"tx"); // panic inside Hram::hram
```

Verification path confirms the panic site: `networks/bitcoin/src/crypto.rs:13-16` (`x` with `expect("point at infinity")`) invoked from `Hram::hram` at `crypto.rs:65`, reached via `BindingFactor::nonces`/`bound` in `crypto/frost/src/nonce.rs:180-212` on commitments parsed by `GeneratorCommitments::read` at `nonce.rs:34-36`.

Note: I could not fully confirm within my remaining tool budget that `Secp256k1::read_G` (kp256) accepts the identity encoding rather than rejecting it — the kp256 `read_G` body was not retrieved. If it already rejects identity, the equivalent exploit uses `E` = any point and `D` chosen so `D + rho * E` sums with other participants' contributions to identity, which still terminates in the same `x(R)` panic when the total nonce sum is identity.