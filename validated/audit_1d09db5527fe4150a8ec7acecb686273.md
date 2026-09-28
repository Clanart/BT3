### Title
Missing identity/zero check on verification shares and group key allows loading a `ThresholdKeys` whose group key is the identity element, permanently DoS-ing (and trivially forging for) that key set - (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes a missing zero-check on a security-critical value (`admin`), where setting it to `address(0)` bricks all authenticated functions with no recovery path. The direct analog in Serai is `ThresholdKeys::new` / `ThresholdKeys::read` in `crypto/dkg`, which never checks that verification shares — and therefore the derived `group_key` — are non-identity. Untrusted bytes fed to `ThresholdKeys::read` can encode identity (`0`) verification shares for participants `1..=t`, producing a `ThresholdKeys` whose `group_key()` is the identity element. That key set is both unusable (unrecoverable DoS, matching the report's impact class) and worse: signatures produced "for" it are signatures under secret key `0`, which anyone can forge.

### Finding Description
`ThresholdKeys::new` computes the group key as the interpolation of the first `t` verification shares:

```rust
// crypto/dkg/src/lib.rs:376-378
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

The only validation performed is share count and participant index bounds (`crypto/dkg/src/lib.rs:355-365`). No check rejects `C::G::identity()` verification shares, a zero `secret_share`, or an identity `group_key` result.

`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads `t`, `n`, `i`, an interpolation method, a `secret_share` via `C::read_F`, and `n` verification shares via `C::read_G`, then calls `ThresholdKeys::new`. `read_F`/`read_G` enforce canonical encodings but do not reject zero/identity. Consequently a serialized blob containing identity points for shares `1..=t` deserializes successfully into keys whose `group_key` is the identity — the elliptic-curve equivalent of `admin = address(0)`.

There is no recovery path: `scale()` explicitly refuses a zero scalar (`crypto/dkg/src/lib.rs:400-406`), and `offset()` can only add to the group key, so once the core is committed to an identity group key the object is stuck. Every `view()`, `AlgorithmSignMachine::sign`, and `complete` built on these keys operates on a "multisig" whose public key is `0`, and signing still proceeds: `sign_share` uses the secret share normally and `verify` checks `sG == R + cA` with `A = identity` (`crypto/schnorr/src/lib.rs:88-109`), i.e. `sG == R` — a relation any party satisfies with `s = r`, so the resulting signatures are universally forgeable, and funds/outputs attributed to that group key are unspendable-by-the-group yet claimable-by-anyone.

Contrast with the rest of the crate, which does enforce the zero-check discipline: `Participant::new` rejects `0` (`crypto/dkg/src/lib.rs:29-35`), `ThresholdParams::new` rejects `t == 0 || n == 0` (`crypto/dkg/src/lib.rs:166-169`), `scale()` rejects a zero scalar, and the dealer asserts the participant scalar is non-zero (`crypto/dkg/dealer/src/lib.rs:34-35`). Verification shares and the resulting `group_key` are the gap.

### Impact Explanation
- **Irrecoverable DoS (the reported class):** a node that loads crafted `ThresholdKeys` bytes has a key set that can never produce signatures valid for a real key; there is no setter/regeneration path within the object, matching "you can't recover it".
- **Forgery/funds impact:** if the identity `group_key` is ever used as a receiving key (e.g., a Bitcoin `Scanner`/output key or an Ethereum address derived from the identity point), signatures "valid" under it are producible by anyone, and outputs scanned for that key are not actually spendable by the validator set.

### Likelihood Explanation
Requires untrusted bytes reaching `ThresholdKeys::read` (restored/corrupted key material or a maliciously constructed serialization). An in-protocol DKG can't produce identity shares for honest participants, so the vector is deserialization of attacker-influenced key material — plausible wherever key blobs are loaded, which is why the original finding was rated Medium: conditional reachability, but total impact once reached.

### Recommendation
In `ThresholdKeys::new`, reject identity verification shares, a zero `secret_share`, and a resulting identity `group_key`:

```rust
// crypto/dkg/src/lib.rs, in ThresholdKeys::new after computing group_key
if bool::from(group_key.is_identity()) || bool::from(secret_share.is_zero()) {
  Err(DkgError::ZeroParameter { t: params.t(), n: params.n() })?; // or a dedicated error
}
for share in verification_shares.values() {
  if bool::from(share.is_identity()) {
    Err(DkgError::InvalidParticipant { n: params.n(), participant: /* idx */ })?;
  }
}
```

This mirrors the existing `Participant`/threshold/scalar zero-checks and closes the deserialization path in `ThresholdKeys::read` automatically.

### Proof of Concept
```rust
// Deserialize a ThresholdKeys<Ristretto> whose first t verification shares are
// the identity point (encoding 0x00..00) and whose secret_share is 0.
// ThresholdKeys::read succeeds because no identity check exists; group_key()
// returns C::G::identity(). Any AlgorithmSignMachine built on it produces
// signatures satisfying sG == R (challenge term vanishes since A = identity),
// which any third party can forge for arbitrary messages, while the validator
// set can never produce a signature under a real key — permanent DoS.
```

Caveat: reachability depends on an integration feeding attacker-controlled bytes to `ThresholdKeys::read`; whether a production component does so is outside the indexed scope.