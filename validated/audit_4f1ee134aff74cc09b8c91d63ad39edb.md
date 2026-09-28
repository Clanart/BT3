### Title
Missing consistency check between deserialized secret share and verification shares in `ThresholdKeys::read` / `ThresholdKeys::new` - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` accepts an attacker-controlled byte stream containing a `secret_share` scalar and a map of `verification_shares`, yet `ThresholdKeys::new` — the only validation performed on the deserialized material — never checks that `C::generator() * secret_share == verification_shares[i]` for the local participant `i`. Analogous to nevado-jms performing no security checks on received messages, Serai performs no security check binding the deserialized private component to the deserialized public components.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) parses `t`, `n`, `i`, an interpolation variant, a `secret_share`, and `n` verification shares, then delegates validation entirely to `ThresholdKeys::new`.

`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) only validates:
- `verification_shares.len() == n` and all participant indexes `<= n` (lines 355-365),
- `Constant` interpolation implies `t == n` (lines 367-374),
- then derives `group_key` as the interpolated sum of `verification_shares[1..=t]` (lines 376-378).

At no point is `secret_share` related to `verification_shares[params.i()]` or to `group_key`. A crafted serialization can therefore pair an arbitrary `secret_share` with arbitrary verification shares, and the resulting `ThresholdKeys` is accepted with an attacker-chosen `group_key`.

Downstream, `ThresholdView` construction in `view()` (crypto/dkg/src/lib.rs:463-533) interpolates this unchecked `secret_share` and signs with it: `secret_share = interpolation_factor(i, included) * scalar * secret_share` plus the ephemeral offset (lines 494-521). `AlgorithmSignMachine::sign` then emits a `SignatureShare` derived from this bogus share. The FROST completion path (`AlgorithmSignatureMachine::complete`, crypto/frost/src/sign.rs:447-495) first tries whole-signature verification and then per-share batch blame — so the crafted key either produces an invalid signature attributed to a wrong participant, or, more importantly, the holder operates under a `group_key` for which no one possesses a consistent secret.

### Impact Explanation
- `ThresholdKeys::read` is the canonical key-material loader (used e.g. by `GeneratedKeysDb::read_keys` in processor/src/key_gen.rs:57-58 and `dkg_recovery::recover_key` inputs). Any workflow where serialized key material is imported, restored, or relayed lets an unprivileged party supply crafted bytes.
- The deserialized `group_key` is fully attacker-controlled (it is computed solely from the attacker-supplied `verification_shares`). Outputs/addresses derived from these keys — including Bitcoin `register_offset`/scanner flows that key off `group_key` — will report funds as received under a key whose secret share is inconsistent with its verification share, i.e., **funds reported received that are not spendable**.
- During signing, `view()` returns a share inconsistent with `verification_shares[i]`, so every produced `SignatureShare` is invalid: the holder is either permanently unable to sign (liveness loss / unspendable key) or is blamed as faulty in `complete()`'s per-share batch verification despite having followed the protocol.

### Likelihood Explanation
Exploitation requires feeding crafted bytes to `ThresholdKeys::read` (or equivalent re-import), which is plausible in key backup/restore, migration, and recovery flows — `dkg_recovery::recover_key` itself only catches the inconsistency at the very end via `G * res != group_key` (crypto/dkg/recovery/src/lib.rs:80-82), after shares have already been summed under attacker-chosen interpolation. The defect is a single missing equality check, and no prior layer validates the binding, so any path that deserializes untrusted key blobs is exposed. Severity: High/Medium (unspendable-key / false-funds-report and signature failure; no direct secret leakage).

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[&params.i()]` (and reject `secret_share == 0`), returning a new `DkgError` variant on mismatch. This binds the private and public halves at deserialization time, before `view()`/`sign`/`recover_key` ever consume the material.

### Proof of Concept
```rust
// Conceptual PoC against ThresholdKeys::read for any Ciphersuite C.
// Craft a serialization where secret_share does not match verification_shares[i]:
//
//   id_len = C::ID.len() (LE u32), id = C::ID
//   t = 2, n = 2, i = 1
//   interpolation = 1 (Lagrange)
//   secret_share = <attacker scalar x>            // x != share implied by shares map
//   verification_shares[1] = G * y1               // arbitrary
//   verification_shares[2] = G * y2               // arbitrary
//
// ThresholdKeys::<C>::read(&mut bytes) returns Ok(...) because
// ThresholdKeys::new checks only counts, participant bounds, and t == n for
// Constant interpolation — never G * x == verification_shares[1].
//
// Result: keys.group_key() = Lagrange-combined y1*G, y2*G (attacker-chosen),
// while original_secret_share() = x is unrelated. keys.view([1,2]).secret_share()
// interpolates x; any produced SignatureShare fails share verification and the
// holder is blamed/never completes, and outputs to group_key() are unspendable.
```