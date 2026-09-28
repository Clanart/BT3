### Title
`ThresholdKeys::read`/`ThresholdKeys::new` derive the group key from a hardcoded `1..=t` participant set without validating that the deserialized shares are mutually consistent - (`crypto/dkg/src/lib.rs`)

### Summary
`ThresholdKeys::new` computes `group_key` by Lagrange-interpolating the verification shares of a hardcoded signing set — always participants `1..=t` — over whatever `verification_shares` map the caller (or, via `ThresholdKeys::read`, an attacker-controlled byte stream) supplies. It only checks that the map has exactly `n` entries with indexes `<= n`; it never verifies that the shares lie on a common polynomial, nor that the stored `secret_share` actually corresponds to `verification_shares[i]`. This is the Serai analog of a hardcoded operator/index limit: a fixed assumption ("the first `t` shares define the group key") baked into the code instead of a consistency check.

### Finding Description
`ThresholdKeys::new` in `crypto/dkg/src/lib.rs` derives the group key as:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

The only validation beforehand is a count/index check (`verification_shares.len() == n`, every key `<= n`). Because `Participant` is non-zero, this forces the key set to be exactly `1..=n`, but places zero constraints on the *points* stored under each index. `ThresholdKeys::read` reconstructs this map directly from an untrusted `io::Read` (`read_G` for each of `1..=n`) and then calls `ThresholdKeys::new`, so a malformed blob is accepted as long as the indexes are in range.

The hardcoded `1..=t` choice means the "group key" is simply the evaluation of the purported shared polynomial at 0, *assuming* all `n` verification shares are co-polynomial. A crafted blob can set `verification_shares[1..=t]` to produce any desired `group_key`, while the remaining `n - t` shares — and the recipient's own `secret_share`/`verification_shares[i]` pair — are completely inconsistent with it. No check catches this: `new` never verifies `C::generator() * secret_share == verification_shares[i]`, and `view()` only interpolates the included set, so a subset of honest shares that happens to be co-polynomial still verifies during signing even though `group_key` was defined by a different, attacker-chosen polynomial.

### Impact Explanation
An unprivileged party that feeds bytes to `ThresholdKeys::read` (explicitly an untrusted-bytes entry point) produces keys whose reported `group_key()` is attacker-chosen and not backed by the actual shares. Where these keys back a wallet/scanner, funds can be reported as received under a group key no participant set can actually sign for — outputs become unspendable. Alternatively, a deserialized key whose `secret_share` doesn't match `verification_shares[i]` produces valid-looking `ThresholdView`s whose signature contributions are silently wrong, failing only at final signature verification with misleading blame. Severity: Medium — it requires feeding a malicious serialized key blob rather than pure protocol messages, but it is reachable through the sanctioned `read` API and yields funds that cannot be spent.

### Likelihood Explanation
Moderate. The flaw triggers whenever serialized `ThresholdKeys` originate from anything other than the local DKG output (restored shares, coordinator/peer-supplied key material, backups). The primitive itself performs no semantic validation, so a single crafted blob deterministically yields an inconsistent key; no race or probabilistic condition is involved. It does not affect keys produced by `dkg_dealer`/`pedpop`/`musig` in-process, which construct consistent maps, so the exposure is limited to deserialization paths.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), validate the deserialized material:
- Check `C::generator() * secret_share == verification_shares[params.i()]`.
- Verify all `n` verification shares are co-polynomial (e.g., for every participant `l`, confirm `verification_shares[l]` equals the evaluation implied by any fixed `t`-subset of shares, or require the caller to prove the DKG transcript instead of trusting bare points).
- Document that the group key is defined by participants `1..=t` so any future change of that hardcoded indexing must be matched by corresponding consistency checks.

### Proof of Concept
```rust
// Crypto: any Ciphersuite, e.g. dalek_ff_group::Ed25519
// 1. Attacker picks an arbitrary desired "group key" secret g and sets
//    verification_shares[1..=t] = points whose Lagrange sum at 0 equals g*G
//    (e.g. all equal g*G/t via constant interpolation, or any fabricated set).
// 2. Attacker fills verification_shares[t+1..=n] and secret_share with
//    unrelated values; i is set to any index whose share does NOT match
//    generator * secret_share.
// 3. Serialize per ThresholdKeys::write layout: ID, t, n, i, interpolation=1,
//    secret_share, then n point encodings.
// 4. Victim calls ThresholdKeys::<C>::read(...) -> Ok(keys), and
//    keys.group_key() == attacker-chosen key.
// 5. Funds sent to an address derived from group_key() are unspendable:
//    no honest t-of-n signing set exists whose shares interpolate to it.
```