### Title
`ThresholdKeys::read` deserializes verification shares without the identity-point rejection enforced elsewhere by `Curve::read_G` - (File: crypto/dkg/src/lib.rs)

### Summary
The bug class in ALPINE-CVE-2026-4360 is a security parameter (`filter='data'`) silently dropped on an uncommon code path (hardlink extraction), letting untrusted input bypass a restriction the caller explicitly requested. The Serai analog: FROST defines `Curve::read_G` to reject identity points specifically because identity nonces/keys break signature and blame logic, yet `ThresholdKeys::read` — an explicitly in-scope deserialization API for untrusted bytes — reads all `n` verification shares with the weaker `Ciphersuite::read_G`, which performs only canonical-encoding checks and accepts the identity point. The safety check is not propagated to this path.

### Finding Description
`Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-100) validates only that the point decompresses and re-encodes canonically; it deliberately permits identity because `Ciphersuite` is a general group abstraction. `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131) layers the FROST-specific invariant on top: `if res.is_identity() { Err("identity point") }`. Every wire point in the FROST signing protocol goes through `Curve::read_G` — `GeneratorCommitments::read` in crypto/frost/src/nonce.rs:35 rejects identity commitments so that `R = D + rho*E` cannot be forced to identity and zero-nonce shares cannot leak the secret share.

`ThresholdKeys::read` in crypto/dkg/src/lib.rs:620-623 instead calls `<C as Ciphersuite>::read_G(reader)` for each of the `n` verification shares:

```rust
let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
```

The resulting map passes straight into `ThresholdKeys::new` (dkg/src/lib.rs:625-631), which checks count and index bounds (`new` at lines 349-391) but never re-checks that shares are non-identity. The verification shares then feed two security-critical paths:

1. `group_key` derivation at dkg/src/lib.rs:376-378 — `group_key = Σ verification_shares[i] * interpolation_factor(i)` for `i in 1..=t`.
2. Interpolated share verification via `view()` at dkg/src/lib.rs:500-506, which produces `verification_shares` consumed by `Algorithm::verify_share` (frost/src/algorithm.rs:89-94) for per-share blame.

A deserialized identity share for participant `l` is therefore accepted, and `l`'s entry in the view reduces to `scalar * λ_l * identity = identity` (plus, for `included[0]`, the `G*offset` term added at line 521).

### Impact Explanation
A party who supplies a `ThresholdKeys` blob to `ThresholdKeys::read` (listed in-scope: "untrusted bytes fed to … `ThresholdKeys::read`") obtains key material whose verification-share map contains an identity element — a state unreachable through `key_gen` (dealer/src/lib.rs:53 always uses `G * share`) and unreachable through any `Curve::read_G`-guarded path.

Concretely, for a participant `l` whose verification share is identity:

- In `view()`, `verification_share(l) = scalar * λ_l * identity = identity` (before the `included[0]` offset adjustment).
- `verify_share` builds pairs whose products must sum to zero: `s_l * G == λ_l * V_l + (D_l + ρ_l * E_l)`. With `V_l = identity`, the equation reduces to `s_l * G == bound_nonce_commitment`. The signer of `l`'s preprocess knows `d_l, e_l` and `ρ_l`, so they can submit `s_l = d_l + ρ_l * e_l` — a share containing **no key-share component at all** — and it verifies. Blame can never identify `l`; the aggregate signature is missing `λ_l * x_l` and fails verification against `group_key`. This is blame-evading signature corruption: every share individually verifies, `complete` produces an invalid signature, and no participant can be faulted.
- Symmetrically, honest `l` is framed: any honest share `s_l = λ_l x_l + d_l + ρ_l e_l` fails the degenerate check unless `x_l = 0`, letting the attacker cause an honest participant to be blamed.

The check that would have prevented this state — identity rejection — exists in the codebase (`Curve::read_G`) but is not propagated to the `ThresholdKeys::read` path, exactly mirroring the CVE's dropped `filter` on the hardlink path.

### Likelihood Explanation
Reachability requires an attacker to influence bytes passed to `ThresholdKeys::read` — e.g., a recovery/import path, or a PedPoP/promote flow where serialized `ThresholdKeys` round-trip through channels an unprivileged party can touch. `Participant` indexes, `t`, `n`, and the interpolation variant are all attacker-controlled fields in the same blob, and none of `ThresholdParams::new`/`ThresholdKeys::new` reject identity shares. The primitive cryptographic validity (canonical points) is preserved, so the malformed keys load without error and the corruption only manifests at signing time as unverifiable/unblamable shares. Medium likelihood: it needs a deserialization surface for keys, but the exploit itself is deterministic once reached. Rated Medium — it yields invalid signatures and broken accountability rather than direct key recovery.

### Recommendation
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:620-623), reject identity verification shares after `Ciphersuite::read_G`, mirroring `Curve::read_G`'s check, or move the identity rejection into a shared helper both paths use. Additionally, `ThresholdKeys::new` should validate `!share.is_identity()` for all entries in `verification_shares` and that the derived `group_key` is non-identity, so invariants hold regardless of which constructor/read path produced the inputs.

### Proof of Concept
```rust
// Attacker crafts a ThresholdKeys blob for victim participant i=1,
// t=2, n=3, with verification_shares[2] = identity.
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(2u16.to_le_bytes());       // t
buf.extend(3u16.to_le_bytes());       // n
buf.extend(1u16.to_le_bytes());       // i = Participant(1)
buf.push(1);                          // Interpolation::Lagrange
buf.extend(x1.to_repr().as_ref());    // attacker-chosen secret_share
buf.extend(V1.to_bytes().as_ref());   // verification share for P1
buf.extend(C::G::identity().to_bytes().as_ref()); // P2 share = IDENTITY
buf.extend(V3.to_bytes().as_ref());   // verification share for P3

// Accepted: Ciphersuite::read_G only enforces canonical encoding
let keys: ThresholdKeys<C> = ThresholdKeys::read(&mut buf.as_slice()).unwrap();

// view() produces verification_shares[2] == λ_2 * scalar * identity == identity.
// P2 (or an attacker controlling P2) later signs with s_2 = d_2 + ρ_2·e_2 —
// no secret-share component — and verify_share still passes, while
// complete() emits a signature that fails against group_key with no
// attributable blame.
```