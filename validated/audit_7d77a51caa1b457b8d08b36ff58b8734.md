### Title
`ThresholdKeys::read` accepts a crafted key set whose `group_key` is fully attacker-controlled, because `ThresholdKeys::new` never checks `secret_share * G == verification_shares[i]` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes attacker-influenced bytes (curve ID, `t`, `n`, `i`, interpolation coefficients, `secret_share`, and `n` verification shares) and forwards them to `ThresholdKeys::new`. `ThresholdKeys::new` is the asserted validation boundary — it checks the verification-share count, participant ranges, and interpolation applicability — but it never validates that the supplied `secret_share` actually corresponds to `verification_shares[i]`, nor that the verification shares interpolate to any coherent key. Consequently the "key" loaded is whatever set of points the byte stream asserted, and `group_key()` is derived purely from the attacker-chosen `verification_shares[1..=t]`.

### Finding Description
`ThresholdKeys::read` (`crypto/dkg/src/lib.rs`, `ThresholdKeys::read`) reads `t`, `n`, `i`, an `Interpolation` (with `n` attacker-chosen coefficients for `Constant`), a `secret_share`, and `n` verification shares, then calls `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`). `ThresholdKeys::new` performs only structural checks:

```rust
// crypto/dkg/src/lib.rs
if verification_shares.len() != usize::from(params.n()) { ... }
for participant in verification_shares.keys().copied() {
  if u16::from(participant) > params.n() { ... }
}
match &interpolation { Constant(_) => t == n check, Lagrange => {} }
let group_key = t.iter().map(|i| verification_shares[i] * interpolation_factor(*i, &t)).sum();
```

There is no check that `C::generator() * secret_share == verification_shares[params.i()]`, and no check that the verification shares are consistent with each other (e.g., a share-verification statement). This is the same defect class as the reference report: a validation routine that is invoked as a security boundary but enforces the boundary unsoundly/incompletely — the invariant "these keys represent shares of `group_key`'s discrete log" is assumed downstream yet never established at deserialization.

A malicious byte stream can therefore supply `verification_shares = {l ↦ a_l·G}` for attacker-known scalars `a_l`, with `t = n` so that `Constant(c)` interpolation factors are also attacker-chosen. The resulting `group_key` is a point whose discrete log the attacker fully knows (they can pick the interpolation coefficients `c` freely, making each effective share `a_l * c_l` known). Meanwhile `secret_share` is set to an arbitrary scalar — the victim's own `original_verification_share(i)` does not equal `secret_share * G`, and nothing notices.

This is reachable per the stated threat model: `ThresholdKeys::read` is the listed untrusted-bytes sink (`ThresholdKeys::read`), and Serai moves serialized `ThresholdKeys` between the DKG/promotion/recovery code paths (`crypto/dkg/promote`, `crypto/dkg/recovery`) and consumers such as the Bitcoin wallet (`networks/bitcoin/src/wallet`), which derives scan addresses and signable keys from `keys.group_key()` (`Scanner::new(key)`, `p2tr_script_buf(key)`, `tweak_keys`).

### Impact Explanation
- **Attacker-known group key**: The deserialized `ThresholdKeys` reports a `group_key()` whose discrete log is known to the attacker (choose `verification_shares[1..=t]` as `a_l·G` and `Interpolation::Constant` coefficients, which `ThresholdKeys::new` accepts without binding them to any secret). The attacker can unilaterally produce valid FROST signatures for this group key — they know every effective share — without any honest participant.
- **Funds theft / unspendable deposits**: Downstream consumers (e.g., `networks/bitcoin/src/wallet/mod.rs` `Scanner`, `SignableTransaction`) treat `group_key()` as the multisig's key for address generation and scanning. Deposits attributed to the victim's key go to an address the attacker controls; the victim's real secret share cannot even produce a consistent signature share (it fails `verify_share` against the fabricated verification share), so the victim neither detects the substitution at load time nor can spend.
- **Silent substitution**: Serialization round-trips normally (`write`/`read` are symmetric over the same unchecked fields), so the corrupted key persists and behaves like a valid key everywhere except it belongs to the attacker.

### Likelihood Explanation
Requires an attacker to feed crafted bytes to `ThresholdKeys::read` — i.e., wherever serialized `ThresholdKeys` transit an untrusted channel (processor key handoff, recovery, restoration from externally stored blobs). No exploitation primitive beyond byte crafting is needed; the missing check is unconditional. If the deployer only ever loads self-produced keys from trusted storage, likelihood drops — but the code marks `read` as a general deserialization entry point and provides no integrity check or authentication binding.

### Recommendation
- In `ThresholdKeys::new` (or at least in `ThresholdKeys::read`, which is the untrusted-input path), verify `C::generator() * secret_share == verification_shares[params.i()]`. This binds the secret share to the public parameters and is the minimal consistency check.
- For `Interpolation::Constant`, also verify the coefficients are consistent with the verification shares (the effective per-participant share is `secret_share * c_i`), or restrict `Constant` deserialization to internally-produced keys.
- Optionally verify the derived `group_key` is non-identity (a crafted `verification_shares[1..=t]` set that interpolates to identity is trivially attacker-known with dlog 0).

### Proof of Concept
```rust
// Attacker crafts bytes consumed by ThresholdKeys::<C>::read:
//   id_len/id: C::ID
//   t = n, i = 1, interpolation = Constant([c_1 .. c_n])  (attacker-chosen scalars)
//   secret_share = arbitrary s
//   verification_shares = { l ↦ a_l * G } for attacker-known a_l, l = 1..=n
//
// ThresholdKeys::new succeeds (count/range/t==n checks all pass).
// keys.group_key() = sum_{l=1..=t} a_l * c_l * G  — discrete log known to attacker.
// keys.original_verification_share(Participant(1)) = a_1 * G != s * G — never checked.
//
// Attacker can sign for keys.group_key() alone (all effective shares known).
// Any Bitcoin wallet/scanner address derived from keys.group_key() credits the attacker.
```

Supporting code references: `ThresholdKeys::new` performs only count/range/interpolation checks and derives `group_key` from `verification_shares[1..=t]` without ever relating it to `secret_share` (`crypto/dkg/src/lib.rs:349-391`); `ThresholdKeys::read` feeds attacker bytes directly into that constructor after reading `n` unchecked `read_G` points (`crypto/dkg/src/lib.rs:604-631`).

Note: exploitability in a given deployment depends on `ThresholdKeys::read` actually consuming attacker-influenced bytes; the cryptographic defect itself (missing share↔verification-share consistency at the validation boundary) is unconditional in the code.