### Title
`ThresholdKeys::read`/`ThresholdKeys::new` never verify the secret share or verification shares are mutually consistent, so crafted key bytes make the node evaluate an attacker-defined sharing whose reported group key is unspendable or attacker-controlled - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to Pycel evaluating an attacker-crafted formula, `ThresholdKeys::read` deserializes a fully attacker-defined "sharing program": an arbitrary `secret_share`, an arbitrary `Interpolation::Constant` coefficient vector, and `n` arbitrary `verification_shares`. `ThresholdKeys::new` then *evaluates* these inputs to derive `group_key` — without ever checking `G * secret_share == verification_shares[i]` or that the verification shares interpolate to a consistent group key across subsets. An unprivileged party that can feed crafted bytes to `ThresholdKeys::read` (the prompt lists it as reachable untrusted input) defines the key the node reports and signs under.

### Finding Description
- `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, an interpolation variant, one scalar `secret_share`, and `n` points `verification_shares`, then calls `ThresholdKeys::new`.
- `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) only checks count/index bounds and that `Constant` requires `t == n`. It then computes `group_key` as `sum(verification_shares[i] * interpolation_factor(i))` for `i in 1..=t` (lines 376-378). It never checks `C::generator() * secret_share == verification_shares[params.i()]`.
- Consequently the deserialized `secret_share` is unrelated to the published `verification_shares`. Two distinct failure modes result:
  1. **Group key defined by shares 1..=t only.** `verification_shares[t+1..=n]` are completely unconstrained. A signing set `included` that contains any index `> t` produces a `ThresholdView` whose interpolated verification shares imply a *different* group key than `group_key()`, so honest members' shares fail `verify` and blame is misattributed, or the aggregate signature never verifies.
  2. **Attacker-chosen group key.** The attacker can choose `verification_shares[1..=t]` consistent with a polynomial whose constant term they know (e.g. shares of a key they generated), while `secret_share` is junk. The node reports a `group_key` whose discrete log the attacker knows — any deposits credited to that key are spendable by the attacker, not the group.
- `interpolation_factor` for `Constant` (lines 226-248) indexes `c[i-1]` directly; the attacker-chosen coefficient vector is the "formula" evaluated over shares in `view` (lines 494-521), letting the attacker select per-participant multipliers arbitrarily.

### Impact Explanation
Funds reported as received to `group_key()` are either unspendable (no party knows the discrete log / shares never produce a valid aggregate) or outright stealable (attacker knows the group key). This matches the accepted impact class "funds reported received that are not spendable" and generalizes the injection class: untrusted bytes are evaluated as a key-derivation formula rather than merely parsed.

### Likelihood Explanation
Reachability requires an attacker to supply the serialized key blob (e.g., via a compromised/malicious key-distribution channel, recovery import, or peer-supplied payload in an integrating protocol that round-trips `ThresholdKeys`). It does not require controlling a threshold of signers, forging a proof, or breaking the DKG — the bytes alone suffice. Conditional on that delivery channel, exploitation is deterministic. Medium.

### Recommendation
In `ThresholdKeys::new`, reject inputs where `C::generator() * secret_share != verification_shares[&params.i()]`. For `Interpolation::Constant`, additionally verify all `n` verification shares are consistent with a single group key (e.g., require `sum` over any/all `t`-subsets equal, or store/check a commitment to the polynomial). Document that `read` must only consume previously trusted bytes, or add a MAC/versioned envelope.

### Proof of Concept
```rust
// Attacker crafts ThresholdKeys bytes for C = Secp256k1 (or any Ciphersuite):
// id_len || C::ID || t=2 || n=3 || i=2
// interpolation = Lagrange (byte 1)
// secret_share = 0xdeadbeef...   // junk, never validated
// verification_shares[1..=3]:
//   attacker picks s1, s2 s.t. group_key = G*(x_attacker) via
//   vs1 = G*a1, vs2 = G*a2 with a1*λ1 + a2*λ2 == x_attacker (they choose a_i)
//   vs3 = arbitrary point (unconstrained, i > t)
let keys = ThresholdKeys::<C>::read(&mut crafted_bytes).unwrap();
// keys.group_key() == G * x_attacker  -> attacker spends any deposit
// keys.view(vec![1,2,3]) produces shares that verify against a different key
assert_ne!(C::generator() * *keys.original_secret_share(),
           keys.original_verification_share(Participant::new(2).unwrap()));
// ^ always true for the crafted blob; never rejected
```