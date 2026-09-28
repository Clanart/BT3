### Title
`ThresholdKeys::new` / `ThresholdKeys::read` accept a `secret_share` that is never validated against the owner's verification share, allowing permanent loss of signing capability for the derived group key - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to Cooler's `transferOwnership` assigning a loan to a lender never proven to implement `CoolerCallback`, Serai assigns signing responsibility for a threshold group key to a holder whose `secret_share` is never proven to actually correspond to that key. `ThresholdKeys::new` derives `group_key` solely from the caller-supplied `verification_shares` map, and stores `secret_share` without ever checking `generator * secret_share == verification_shares[params.i()]`. The same gap is reachable from untrusted bytes via `ThresholdKeys::read`, which reconstructs the object from serialized fields and forwards to `new` with no self-consistency check.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::new` performs only structural validation:

- `verification_shares.len() == n` (lines 355–360)
- every key in the map is `<= n` (lines 361–365)
- `Constant` interpolation requires `t == n` (lines 367–374)

It then computes `group_key = Σ verification_shares[i] * interpolation_factor(i, 1..=t)` over participants `1..=t` (lines 376–378) and stores the supplied `secret_share` verbatim (lines 380–390). There is no check that the holder's own share is consistent: `C::generator() * secret_share == verification_shares[&params.i()]` is never evaluated.

`ThresholdKeys::read` (lines 574–632) deserializes `t`, `n`, `i`, an `Interpolation` (with `Constant` reading `n` attacker-controlled scalars), a `secret_share` scalar, and `n` verification-share points, then calls `new`. An attacker who can supply these bytes (key backup/keystore import, or any path feeding untrusted serialized keys) constructs a `ThresholdKeys` whose `group_key` is fully attacker-chosen while the embedded `secret_share` is unrelated.

During signing, each participant's share is verified against its verification share, so the malformed holder can never produce a valid partial signature — exactly like the Cooler lender who can't implement `onRepay`/`onRoll`/`onDefault`. But `group_key()` still returns the attacker-selected key, and anything keying deposits/scanning off it (e.g., `tweak_keys` then `Scanner::new(keys.group_key())` in `networks/bitcoin/src/wallet/mod.rs:46-75` and `processor/src/networks/bitcoin.rs:314-347`) will treat outputs to that key as owned.

### Impact Explanation
Funds addressed to the `group_key` of such a `ThresholdKeys` are unspendable: no threshold coalition including this holder can produce a valid signature, because the holder's share fails share verification, and (for `t == n` MuSig/Constant-interpolation keys) the group key is unrecoverable outright. This is a permanent bricking of received funds — the accepted "funds reported received that are not spendable" impact — caused by transferring "ownership" of the group key to a share that was never validated as capable, mirroring the Cooler finding's broken-loan outcome.

### Likelihood Explanation
Requires an attacker to feed crafted serialized `ThresholdKeys` (or a `new` call) to a victim — plausible wherever key material is imported rather than generated in-process, and explicitly an untrusted-byte sink per the audit surface (`ThresholdKeys::read`). The check is a single scalar multiplication and is present in spirit elsewhere (e.g., `musig` recomputes `our_pub_key` and requires membership in `keys` at `crypto/dkg/musig/src/lib.rs:121-124`), so its absence here is an inconsistency rather than a design choice. Medium severity, matching the source finding.

### Recommendation
In `ThresholdKeys::new`, after the existing participant checks, add:

```rust
if verification_shares[&params.i()] != C::generator() * secret_share.deref() {
  Err(DkgError::InvalidSecretShare)?
}
```

This propagates automatically to `ThresholdKeys::read`, guaranteeing any deserialized or constructed key set is internally consistent before a `group_key` can be advertised.

### Proof of Concept
```rust
// Ristretto example; any Ciphersuite works.
let n = 3u16; let t = 2u16;
// Honest-looking verification shares
let real: Vec<Scalar> = (0..n).map(|_| Scalar::random(&mut OsRng)).collect();
let mut vs = HashMap::new();
for i in 1..=n { vs.insert(Participant::new(i).unwrap(), Ristretto::generator() * real[(i-1) as usize]); }

// Attacker swaps participant i=1's secret share for an unrelated scalar
let bad_secret = Zeroizing::new(Scalar::random(&mut OsRng));
let keys = ThresholdKeys::<Ristretto>::new(
  ThresholdParams::new(t, n, Participant::new(1).unwrap()).unwrap(),
  Interpolation::Lagrange,
  bad_secret,            // != real[0]; never checked
  vs,
).unwrap();

// group_key is well-formed and deposits to it are scannable/spendable in principle,
// yet participant 1's share fails verification in FROST sign, and for t==n variants
// the key is entirely unspendable.
```

The serialized path is identical: write a `ThresholdKeys` blob via `write` with `secret_share` replaced by an arbitrary scalar — `ThresholdKeys::read` accepts it and returns a `group_key` no one can sign for.