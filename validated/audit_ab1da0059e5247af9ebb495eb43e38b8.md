### Title
`ThresholdKeys::read` accepts a forged key file — attacker-chosen `verification_shares`/`secret_share` silently replace the real threshold key - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The Ansible report's bug class is *a privileged consumer silently operating on content wholly replaced by an unprivileged party* (the `user` module writing attacker-controlled bytes to an arbitrary path). The Serai analog is `ThresholdKeys::read` / `ThresholdKeys::new`: deserializing a `ThresholdKeys` blob never verifies that the embedded `secret_share` corresponds to the embedded `verification_shares` (`G * secret_share == verification_shares[i]`), nor that the shares derive from a common polynomial. An attacker who can supply the serialized key material bytes can therefore silently substitute an entirely attacker-known key set, and the honest signer will use it believing it is its DKG output.

### Finding Description
`ThresholdKeys::read` deserializes `(t, n, i)`, the interpolation method, `secret_share`, and `n` `verification_shares`, then calls `ThresholdKeys::new` (crypto/dkg/src/lib.rs, `ThresholdKeys::read` ~lines 573–632). `ThresholdKeys::new` only validates:

- `verification_shares.len() == n` and each index `<= n` (lines 355–365),
- `Constant` interpolation requires `t == n` (lines 367–374),

then computes `group_key` by interpolating `verification_shares[1..=t]` (lines 376–378) and stores everything in a `ThresholdCore` — **without ever checking `C::generator() * secret_share == verification_shares[i]`** and without checking that the `verification_shares` lie on a degree-`t-1` polynomial (e.g., `Constant` interpolation coefficients are read from the blob itself but never tied to the shares).

So every field of the key file is accepted verbatim. An attacker who controls the serialized bytes can:

1. Keep the honest `secret_share` but replace all `verification_shares` with attacker-chosen points → `group_key()` yields an attacker-defined key while `view()` produces shares that sign against attacker-controlled verification shares. `sign_share`/`verify_share` in `crypto/frost/src/algorithm.rs` verify shares against `view.verification_shares` — also attacker-controlled — so internally "valid" signatures can be produced for a group key the honest participants never agreed to.
2. Replace `secret_share` and `verification_shares` together with a fully attacker-known polynomial → the loaded `ThresholdKeys` is an attacker-owned key in its entirety; the privileged holder will `preprocess`/`sign`/`complete` valid signatures for the attacker's group key.
3. Keep honest shares but claim different `t`/`n`/`i` → cause the signer to interpolate against the wrong Lagrange basis, producing signature shares that validate under the forged `verification_shares` map.

This mirrors CVE-2024-9902 exactly: the integrity of content consumed under privilege is never authenticated against the identity/context it claims (no MAC, no consistency proof, no binding to the DKG transcript), so a low-privilege write becomes a full silent replacement.

### Impact Explanation
Critical-to-High depending on deployment:

- **Unintended signatures / key substitution**: a node loading attacker-substituted `ThresholdKeys` will produce valid FROST signatures for an attacker-controlled group key — arbitrary messages/transactions signed under a key the attacker fully knows.
- **Unspendable funds**: if only `verification_shares` are replaced while `secret_share` stays honest, `group_key()` addresses can receive funds that no honest signing set can ever spend (shares don't satisfy the verification map), a direct loss-of-funds/DoS.
- No collusion or validator misbehavior is required — the attacker's only capability is supplying the bytes fed to `ThresholdKeys::read`, exactly the accepted reachability model for `*_::read` APIs.

### Likelihood Explanation
Reachability is the question. In Serai's own processor, keys come from `musig()`/DKG output rather than attacker bytes, so exploitation requires an integrator or operational path where the key file is loaded from storage an unprivileged party can influence — the same precondition as the Ansible advisory (unprivileged write, privileged consumption), which is what makes it a *Medium-class* analog rather than automatic compromise. The missing-check root cause itself is unconditional and deterministic.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`, which controls untrusted input):

1. Assert `C::generator() * secret_share == verification_shares[&params.i()]` — reject blobs where the private share doesn't match the public share.
2. For `Interpolation::Constant(c)`, verify `verification_shares[j] == sum_k c[k] * j^k` (or at minimum that `sum_k c[k] * G` reconstructs participant shares); for `Lagrange`, consider committing `verification_shares` to the DKG transcript so tampering is detectable.
3. Prefer binding the serialized keys to their provenance (e.g., store alongside or MAC with a key derived from the DKG `context`), so a substituted blob fails authentication rather than being silently adopted.

### Proof of Concept
Conceptual, against `ThresholdKeys::read`/`new`:

```rust
// Attacker generates their own key material
let evil_params = ThresholdParams::new(t, n, i).unwrap();
let evil_secret = Zeroizing::new(C::random_nonzero_F(&mut rng));
let mut evil_shares = HashMap::new();
// e.g. constant polynomial f(j) = s  -> all shares identical
for j in 1..=n {
    evil_shares.insert(Participant::new(j).unwrap(), C::generator() * evil_secret.deref());
}
// Serialize a ThresholdKeys blob: C::ID, t, n, i, interpolation byte,
// evil_secret, then evil_shares[1..=n] — all attacker-chosen.
let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// keys.group_key() == G * s — a key the attacker knows in full.
// The consumer now signs with attacker-owned material; no error is raised.
```

For the funds-lock variant: keep the honest `secret_share`, overwrite `verification_shares` with arbitrary points — `read` succeeds, `group_key()` changes, and every subsequent `view()`/`sign` produces shares that "verify" against the forged map while the real group's funds at that address are unspendable.

Caveat: I confirmed `ThresholdKeys::new` performs no share-consistency check and that `read` feeds attacker bytes directly into it. I did not fully enumerate every upstream caller's authentication of the stored blob, so the exploitability rating depends on whether the key file is ever writable by a less-privileged party — the code-level defect (silent acceptance of forged key material) is confirmed.