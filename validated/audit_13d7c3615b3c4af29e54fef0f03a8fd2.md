### Title
Identity verification shares accepted via `ThresholdKeys::read` bypass the `Curve::read_G` identity rejection, allowing deserialization of a key set whose group key has a known discrete logarithm - (File: crypto/dkg/src/lib.rs)

### Summary
Serai has two decode paths for group elements. The FROST-facing path, `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`), wraps `Ciphersuite::read_G` and additionally rejects the identity point — this is the "middleware check". The DKG serialization path, `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:620-623`), decodes every verification share through `<C as Ciphersuite>::read_G` directly, which only enforces canonical encoding (`crypto/ciphersuite/src/lib.rs:91-101`) and happily accepts the identity. An attacker-supplied `ThresholdKeys` blob therefore reaches a semantically forbidden state — verification shares equal to the identity — through a transport variant that the intended check never sees, the same class as a middleware rule that doesn't cover `.rsc`/segment-prefetch route variants.

### Finding Description
- `Curve::read_G` explicitly rejects identity: `if res.is_identity().into() { Err(...) }` (`crypto/frost/src/curve/mod.rs:125-131`). All signing-protocol inputs — `Commitments::read`, `read_preprocess`, `read_share`-adjacent point reads (`crypto/frost/src/nonce.rs:34-36,133-139`) — route through it, so no nonce commitment or peer-supplied point can ever be the identity.
- `ThresholdKeys::read` does not. It reads `n` verification shares with `<C as Ciphersuite>::read_G(reader)?` (`crypto/dkg/src/lib.rs:620-623`) and then calls `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:625-631`).
- `Ciphersuite::read_G` only checks `from_bytes` succeeds and that the re-encoding is canonical (`crypto/ciphersuite/src/lib.rs:91-101`). The identity point is a canonical, prime-subgroup element, so it passes.
- Consequence: a serialized `ThresholdKeys` can encode `secret_share = 0` with all verification shares equal to the identity, or `Interpolation::Constant` with all-zero coefficients (`crypto/dkg/src/lib.rs:604-613`). Every per-share consistency equation `G * share_i == verification_share_i` then holds (`0 == identity`), the recovered group key is the identity, and the secret behind it is `0` — a key whose discrete logarithm is publicly known. The FROST code itself acknowledges this state is reachable only via deserialization: `"The only known way to cause this ... is to deserialize a semantically invalid FrostKeys"` (`crypto/frost/src/sign.rs:491-494`), confirming the invariant is expected but not enforced on this path.

### Impact Explanation
`ThresholdKeys::read` is explicitly in-scope as a sink for untrusted bytes. Any component that imports or restores threshold keys supplied by an unprivileged party (key-share migration, backup restore, coordinator-provided blobs) will accept a key set whose group key is the identity / a function of attacker-chosen zero shares. The holder can then run `AlgorithmMachine::sign` and produce signature shares that pass `verify_share` (the share verification equations are all satisfied by the zero/identity construction), yielding valid Schnorr signatures under a group key whose secret is known to the attacker — a signature-forgery / unauthorized-"protected-resource" outcome reachable solely because the identity rejection was applied on the signing-input path but not on the key-deserialization path.

### Likelihood Explanation
Medium. Exploitation requires a deployment that feeds attacker-influenced bytes to `ThresholdKeys::read` and then trusts the embedded `group_key`/shares (rather than cross-checking the group key against an externally rooted value). Where keys are loaded from self-generated storage the blob is not attacker-controlled. The bypass itself is deterministic — no probabilistic or adversarial-computation requirement — and the divergent check is a direct code-level fact. Uncertainty: whether `ThresholdKeys::new` independently rejects all-zero `Constant` interpolations or identity shares was not fully verified from the indexed source; if it does, severity drops to Low/informational.

### Recommendation
In `ThresholdKeys::read` (and `ThresholdKeys::new` for defense-in-depth), decode verification shares via `Curve::read_G`-equivalent logic that rejects the identity, and reject `Interpolation::Constant` coefficient vectors that are all zero. Apply the same identity check inside `ThresholdKeys::new` so that even programmatically constructed keys cannot represent a zero-secret group. Aligning both decode variants on the same invariant mirrors the upstream fix of applying middleware matchers to all transport variants.

### Proof of Concept
```rust
// Construct a ThresholdKeys blob for C = Ristretto (or any Curve) with:
//   id_len/id matching C::ID
//   t = 1, n = 1, i = 1
//   interpolation = 0 (Constant) with 1 coefficient = 0
//   secret_share = 0
//   verification_shares[1] = identity (canonical compressed identity bytes)
let mut blob = vec![];
blob.extend((C::ID.len() as u32).to_le_bytes());
blob.extend(C::ID);
blob.extend(1u16.to_le_bytes()); // t
blob.extend(1u16.to_le_bytes()); // n
blob.extend(1u16.to_le_bytes()); // i
blob.push(0);                    // Interpolation::Constant
blob.extend(C::F::ZERO.to_repr());         // coefficient 0
blob.extend(C::F::ZERO.to_repr());         // secret_share = 0
blob.extend(C::G::identity().to_bytes());  // verification_share = identity

// This parse succeeds: each share is read with Ciphersuite::read_G, which
// accepts the identity. Curve::read_G would have rejected it.
let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity()));

// keys can now drive AlgorithmMachine::new(...).preprocess/sign; every
// share-verification equation holds, and signatures verify under a group
// key with known discrete log (0).
```