One more targeted check on `ThresholdView` offset handling and the Schnorr `sign_share`/`verify` path.### Title
MuSig DKG accepts the identity point as a participant key, creating a secretless "backdoor" signer seat - (File: crypto/dkg/musig/src/lib.rs)

### Summary
`musig` (Serai's n-of-n non-interactive DKG) validates the submitted key list in `check_keys` only for emptiness, length, and byte-level duplicates. It never rejects the identity point. A participant whose public key is the identity has a secret share of `0`, so an attacker can occupy a seat in the aggregated key — and sign for it — without knowing any discrete logarithm, analogous to a hardcoded owner backdoor that hands an unauthorized party a privileged position in the "ownership" set.

### Finding Description
`check_keys` (`crypto/dkg/musig/src/lib.rs:46-63`) iterates `keys` and inserts each `key.to_bytes()` into a `HashSet`, erroring only on `DuplicatedParticipant`. There is no `is_identity` check (contrast `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131`, which explicitly rejects identity points — but `musig` receives already-parsed `C::G` values, not bytes through that path).

Consequences of an identity entry at index `j`:

- Aggregation: `group_key = Σ binding_factor_i · K_i` contributes `b_j · 0 = 0` for that seat, so the identity seat silently adds nothing to the group key (`musig_key_multiexp`, lines 87-98).
- `musig` (lines 116-162): an attacker calling `musig(context, Zeroizing::new(C::F::ZERO), keys)` gets `our_pub_key = G·0 = identity`, `keys.iter().position` finds it, and `ThresholdKeys::new` succeeds — `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) validates only counts, participant ranges, and interpolation applicability, never share/identity relations.
- Signing (`crypto/frost/src/sign.rs:283-411`, `crypto/frost/src/algorithm.rs:201-211`): the attacker's signature share is `s = nonce + c·(0·λ·b) = nonce`. `verify_share` checks `s·G == R + c·(verification_share·factor)`; with `verification_share = identity` this reduces to `s·G == R`, which the attacker satisfies trivially with any nonce commitment.

So a purported n-of-n group (e.g., 2-of-2 with a second guardian) can be instantiated by a single attacker controlling seat 1 with a real key and seat 2 with the identity: they hold valid `ThresholdKeys` for every seat and can produce complete, verifying signatures under the aggregate `musig_key` alone.

### Impact Explanation
Any counterparty, coordinator, or protocol that builds a MuSig aggregate over an externally supplied key list (e.g., `musig_key_vartime`/`musig` on validator-provided public keys, as in `coordinator/src/tributary/signing_protocol.rs:118-121`) and trusts that each listed key represents an independent, secret-holding signer is wrong: an identity key is a seat that requires no consent from anyone but the attacker. The attacker unilaterally signs messages/transactions under a group key victims believe is jointly controlled — equivalent to the incident's attacker taking ownership and minting claims at will, here minting valid threshold signatures.

### Likelihood Explanation
Requires the attacker to influence the key list fed to `musig`/`musig_key_vartime` — public, untrusted input by design (participant key registration). No secret leakage, collusion, or broken transport assumption is needed; the only precondition is that the pipeline admits an identity-encoded public key, which nothing in `check_keys`, `ThresholdKeys::new`, or the FROST sign path prevents. Wherever keys arrive via `Ciphersuite::read_G` (rather than `Curve::read_G`), an identity encoding may pass decoding as well.

### Recommendation
Reject identity (and generally low-order/non-canonical) points in `check_keys` before aggregation:

```rust
for key in keys {
  if bool::from(key.is_identity()) {
    Err(MusigError::DuplicatedParticipant(*key))?; // or a dedicated InvalidKey variant
  }
  let bytes = key.to_bytes().as_ref().to_vec();
  if !set.insert(bytes) {
    Err(MusigError::DuplicatedParticipant(*key))?;
  }
}
```

Also consider asserting `secret_share != 0` / `verification_shares` non-identity in `ThresholdKeys::new` as defense-in-depth, since `ThresholdKeys::read` deserializes attacker-influenced bytes into the same structure without such checks.

### Proof of Concept
```rust
// Crypto-scheme-level sketch (Ristretto)
use ciphersuite::Ciphersuite;
use dkg_musig::*; // musig, musig_key_vartime
use frost::{Participant, ThresholdKeys};
use frost::tests::{key_gen /* not needed */, sign_without_caching};
use frost::algorithm::{AlgorithmMachine, IetfSchnorr /* Schnorr wrapper */};

let ctx = [7u8; 32];

// Attacker's real key plus an identity "second signer"
let a = Zeroizing::new(<Ristretto as Ciphersuite>::F::random(&mut OsRng));
let keys = vec![
  Ristretto::generator() * *a,
  Ristretto::G::identity(),               // passes check_keys: unique, non-empty
];

let group_key = musig_key_vartime::<Ristretto>(ctx, &keys).unwrap();
// group_key = b1*A + b2*0; attacker needs no external party

// Seat 1: real key. Seat 2: private_key = 0 -> pub = identity -> found in list
let t1 = musig::<Ristretto>(ctx, a.clone(), &keys).unwrap();
let t2 = musig::<Ristretto>(ctx, Zeroizing::new(<Ristretto as Ciphersuite>::F::ZERO), &keys).unwrap();
assert_eq!(t1.group_key(), t2.group_key());

// Run standard AlgorithmSignMachine flow for participants 1 and 2 on msg.
// Seat 2's share is s2 = nonce2 + c * (0 * bf2) = nonce2; verify_share checks
// s2*G == R2 + c*identity*bf2 == R2 — satisfied for free.
// Aggregated signature verifies under `group_key`: sole-attacker signature on a
// key presented as 2-of-2.
```

Note: I could not fully verify whether upstream callers pre-filter identity points (e.g., whether validator key deserialization already rejects identity); if all inputs reach `musig` only through `Curve::read_G` or equivalent identity-rejecting paths, reachability is reduced and this drops to defense-in-depth. Within the in-scope crates themselves, however, no check exists.