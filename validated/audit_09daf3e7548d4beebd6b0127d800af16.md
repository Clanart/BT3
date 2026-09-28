### Title
Untrusted `ThresholdKeys` deserialization never verifies the secret share against the verification shares, allowing keys whose group key cannot be signed for - (File: crypto/dkg/src/lib.rs)

### Summary
The analog of CVE-2026-38950 (unsafe deserialization of a crafted model file producing attacker-controlled state) in Serai is `ThresholdKeys::read`. It reconstructs full threshold key material from a byte blob — `t`, `n`, `i`, the interpolation coefficients, the secret share, and all `n` verification shares — and passes them to `ThresholdKeys::new`, which derives `group_key` purely from the verification shares. Nothing ever checks that the deserialized `secret_share` is consistent with `verification_shares[i]` (i.e., `secret_share * G == verification_shares[i]`), so a crafted blob yields a structurally valid `ThresholdKeys` whose group key does not correspond to the secret share it carries.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, an `Interpolation` (including `n` attacker-controlled constant coefficients), a `secret_share` scalar, and `n` verification shares, then calls `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391). `new` only checks:

- `verification_shares.len() == n` and all participant indexes `<= n` (lines 355-365),
- `Constant` interpolation requires `t == n` (lines 367-374),
- then computes `group_key` as `sum over participants 1..=t of verification_shares[i] * interpolation_factor(i)` (lines 376-378).

The deserialized `secret_share` is stored verbatim and never validated against `verification_shares[params.i()]`. In the honest DKG flow (`KeyMachine::calculate_share` in crypto/dkg/pedpop/src/lib.rs:515-528) each verification share is either `G * secret` (for self) or derived via Pedersen/Feldman commitments, so consistency holds by construction. On the deserialization path that invariant is assumed but never enforced — the exact "crafted file loaded without validation" shape of the reported CVE.

### Impact Explanation
A crafted serialized `ThresholdKeys` blob produces keys where `group_key` — the address wallets/credit the funds to — is a function of attacker-chosen verification shares, while `secret_share` is unrelated. When the holder later signs via `ThresholdView`/`view()` (crypto/dkg/src/lib.rs:463-533), the interpolated secret share does not match `verification_shares[i]`, so FROST signature-share verification by cosigners fails, or worse, shares verify against a group key the secret shares cannot actually authorize. The result is keys that report a group key for which no valid threshold signature can be produced — funds credited to that key are not spendable (an explicitly in-scope impact class). A crafted blob can also smuggle arbitrary `Constant` interpolation coefficients and verification shares, giving the writer full control over the derived `group_key` while the victim believes they hold a key for it.

### Likelihood Explanation
`ThresholdKeys::read`/`serialize` are the persistence and transport format for threshold key material; any flow where serialized keys are imported (recovery, migration, backup restore, `promote`/`recovery` crate hand-offs) trusts the blob's internal consistency. An attacker able to supply or tamper with that blob (the same trust assumption as the CVE's "checkpoint files loaded from session directories") causes silent inconsistency: deserialization succeeds, `group_key()` returns normally, and the failure only surfaces as permanently unspendable funds or failed signing. No malformed-encoding rejection occurs because every field is individually canonical.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), verify `C::generator() * secret_share == verification_shares[params.i()]` before accepting the keys, rejecting blobs where the secret share is inconsistent with the committed verification shares. Also consider validating that `secret_share` is non-zero and that constant-interpolation coefficient counts match `n` semantics even when `t == n`.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::read accepts inconsistent material
use dkg::{ThresholdKeys, Participant, Interpolation};
use ciphersuite::{Ciphersuite, group::ff::PrimeField};
use dalek_ff_group::{Ristretto, Scalar, EdwardsPoint};
use zeroize::Zeroizing;

// Attacker crafts a blob: t = n = 1, i = 1, Lagrange interpolation.
// secret_share = s (attacker-chosen), verification_shares[1] = V where V != G*s.
let s = Scalar::from(42u64);
let unrelated = Scalar::from(7u64);          // V = G*unrelated != G*s

let mut blob = vec![];
blob.extend((Ristretto::ID.len() as u32).to_le_bytes());
blob.extend(Ristretto::ID);
blob.extend(1u16.to_le_bytes());             // t
blob.extend(1u16.to_le_bytes());             // n
blob.extend(Participant::new(1).unwrap().to_bytes()); // i
blob.push(1);                                // Interpolation::Lagrange
blob.extend(s.to_repr().as_ref());           // secret_share (inconsistent)
blob.extend((EdwardsPoint::generator() * unrelated).to_bytes()); // verification_shares[1]

// Deserialization succeeds despite the inconsistency.
let keys = ThresholdKeys::<Ristretto>::read(&mut blob.as_slice()).unwrap();
// group_key is derived solely from the malicious verification share...
assert_eq!(keys.group_key(), EdwardsPoint::generator() * unrelated);
// ...yet secret_share * G != verification_shares[i]:
assert_ne!(
  EdwardsPoint::generator() * *keys.original_secret_share(),
  keys.original_verification_share(Participant::new(1).unwrap()),
);
// Any signature produced from `keys.view(...)` fails verification under group_key,
// leaving funds sent to group_key unspendable.
```

The gap: crypto/dkg/src/lib.rs:376-384 computes `group_key` from verification shares and returns `Ok` without ever comparing `secret_share * generator()` to `verification_shares[i]`.