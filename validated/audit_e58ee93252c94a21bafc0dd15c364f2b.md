### Title
Untrusted `ThresholdKeys::read` bytes can arbitrarily redefine the signing group — no consistency check binds the secret share to the verification shares or group key - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The PrestaShop advisory (CWE-284) describes an unauthenticated write path allowing an unprivileged party to modify any value in a trusted configuration store. The Serai analog is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg/src/lib.rs`: every field that defines the multisig's identity — `t`, `n`, own participant index `i`, the interpolation method and its constant coefficients, the secret share, and all `n` verification shares — is taken verbatim from attacker-controlled bytes, and `ThresholdKeys::new` derives `group_key` purely from `verification_shares[1..=t]` without ever checking that the deserialized `secret_share` corresponds to `verification_shares[params.i()]` or that the verification shares are mutually consistent (e.g., lie on a degree-`t-1` polynomial). The parser is in-scope as an untrusted-bytes sink (`ThresholdKeys::read`).

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, an interpolation selector (with `n` free-form constant coefficients when `0`), a secret share, and `n` group elements, then calls `ThresholdKeys::new`. `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) performs only shape checks:

- `verification_shares.len() == n` and participant indexes `<= n` (lines 355-365),
- `Constant` interpolation only when `t == n` (lines 367-374).

It then computes `group_key = Σ_{i=1..=t} verification_shares[i] * interpolation_factor(i, {1..=t})` (lines 376-378) and stores everything without verifying `C::generator() * secret_share == verification_shares[params.i()]`. The `debug_assert_eq!`-style checks that would catch this in honest flows (e.g., `debug_assert_eq!(keys.group_key(), group_key)` in the dealer at crypto/dkg/dealer/src/lib.rs:64, and `debug_assert_eq!(musig_key_vartime(..), Ok(group_key))` in crypto/dkg/musig/src/lib.rs:153) confirm that the invariant is *assumed* from the producing protocol, not *enforced* on deserialization. Contrast with `dkg::recovery::recover_key` (crypto/dkg/recovery/src/lib.rs:80-82), which does check `C::generator() * res == group_key` before returning — showing the codebase knows this class of consistency check is necessary, but omits it on the deserialization path.

The deserialized `ThresholdKeys` is then used to construct `ThresholdView`s (`view`, crypto/dkg/src/lib.rs:463-533), FROST signing machines (`crypto/frost/src/sign.rs:283-312`), and — for Secp256k1 — `tweak_keys`/`Scanner`/`SignableTransaction` in the Bitcoin wallet layer (networks/bitcoin/src/wallet/mod.rs:46-75, 153-228).

### Impact Explanation
An attacker who can supply the serialized key blob to a participant (e.g., a restored/provisioned key file, a migrated validator, a backup import — anywhere `ThresholdKeys::read` consumes bytes not produced by a trusted local DKG) can write arbitrary "configuration" into the security-critical state:

1. **Group key forgery / fund theft.** Set `t = 1`, `n = 1`, supply `secret_share = s` and `verification_shares[1] = s*G` for an attacker-known `s` (or even just attacker-chosen `verification_shares`, since `group_key` is computed solely from them). The node adopts `group_key` as the multisig key; any subsequent `tweak_keys`/`Scanner` operation scans deposits to an attacker-controlled Bitcoin key, and any FROST signature the node emits is over the attacker's key. Funds "received" to this key are spendable by the attacker, not by the intended group.
2. **Share/key desynchronization → wrongful blame.** Supply a `secret_share` inconsistent with `verification_shares[i]`. The node joins signing sessions believing it is a valid participant; its signature shares fail `verify_share` (which checks the share against `view.verification_share(l)`, crypto/dkg/src/lib.rs:503-506), producing `FrostError::InvalidShare` (crypto/frost/src/sign.rs:45-46) against an innocent-looking participant and feeding the blame/slashing pipeline.
3. **Participant index reassignment.** `i` is read from the byte stream (`Participant::new(read_u16()?)`, line 600) and only bounded by `i <= n`; an attacker can relabel which participant index the node believes it owns, shifting it into a different Lagrange position and corrupting every `view(included)` interpolation.

Each of these is a write of arbitrary values into the node's authoritative view of the multisig by an unprivileged input — the same bug class as the referenced advisory.

### Likelihood Explanation
Medium. Exploitation requires the attacker to control the bytes passed to `ThresholdKeys::read`, i.e., an environment where key material is imported from an untrusted or unauthenticated channel (backup restore, provisioning service, network-delivered key package). That is a realistic integrator pattern rather than an exotic one, and the crate exposes `read`/`serialize` as the canonical persistence mechanism with no integrity/authenticity framing or checksum of `group_key` against an external anchor. The impact on a successfully attacked node is high (signing under an attacker-chosen key, unspendable/stolen deposits, erroneous blame).

### Recommendation
- In `ThresholdKeys::new` (or at least in `ThresholdKeys::read`), verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject otherwise.
- Verify the `n` verification shares are mutually consistent for the claimed interpolation: for `Lagrange`, check that the shares interpolate to a polynomial of degree `t - 1` (e.g., that `group_key` computed from *any* `t`-subset agrees, or spot-check via DLEq/evaluation); for `Constant`, verify `group_key == Σ c[i] * verification_shares[i+1]`-style consistency against the deserialized coefficients.
- Bind the serialization to an externally committed group key: include `group_key` in the written form and require callers to confirm it against the value produced by the DKG/key-gen session before accepting a deserialized `ThresholdKeys`.
- Document that `ThresholdKeys::read` input must be authenticated (e.g., MAC'd or loaded only from locally-produced storage), since parsing alone cannot establish provenance.

### Proof of Concept
```rust
// Standalone demonstration against crypto/dkg/src/lib.rs
// An attacker crafts bytes that ThresholdKeys::read accepts, installing an
// attacker-controlled group key inconsistent with any honest DKG output.

use ciphersuite::{group::GroupEncoding, Ciphersuite};
use ciphersuite_kp256::Secp256k1; // or any in-scope ciphersuite
use dkg::{Participant, ThresholdKeys};
use zeroize::Zeroizing;
use group::ff::Field;

fn main() {
    // Attacker picks a secret they fully know
    let attacker_secret = <Secp256k1 as Ciphersuite>::F::from(0xdead_beefu64);
    let attacker_point = Secp256k1::generator() * attacker_secret;

    // Forge a serialized ThresholdKeys: t=1, n=1, i=1, Lagrange,
    // secret_share = attacker_secret, verification_shares[1] = attacker_point
    let mut blob = Vec::new();
    let id = <Secp256k1 as Ciphersuite>::ID;
    blob.extend((id.len() as u32).to_le_bytes());
    blob.extend(id);
    blob.extend(1u16.to_le_bytes()); // t
    blob.extend(1u16.to_le_bytes()); // n
    blob.extend(Participant::new(1).unwrap().to_bytes()); // i = 1
    blob.push(1u8); // Interpolation::Lagrange
    blob.extend(attacker_secret.to_repr().as_ref());
    blob.extend(attacker_point.to_bytes().as_ref());

    // Deserialization succeeds with NO check that these values came from a DKG
    let keys = ThresholdKeys::<Secp256k1>::read(&mut blob.as_slice()).unwrap();

    // The node now believes the attacker-controlled key is the multisig group key
    assert_eq!(keys.group_key(), attacker_point);

    // Worse: values need not be self-consistent at all. Set
    // verification_shares[1] = X (attacker chosen) but secret_share = garbage.
    // keys.group_key() reports X while view()/sign produce shares for a
    // different secret — every emitted share fails verify_share and the node is
    // blamed as faulty (FrostError::InvalidShare), or, when the blob is
    // self-consistent as above, the node signs for a key the attacker owns.
}
```

The `assert_eq!` at the end demonstrates the core flaw: `ThresholdKeys::read` accepts a byte stream that no honest protocol path (`dealer::key_gen`, `pedpop`, `musig`) could ever produce for this node's real secret material, yet the resulting object is indistinguishable from a legitimate one at the type level — a missing-authenticity check on a write into security-critical configuration, matching the referenced advisory's class.