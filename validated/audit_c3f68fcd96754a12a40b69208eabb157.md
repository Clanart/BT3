### Title
Panic on out-of-range Participant index (`Participant(0)`) in `AlgorithmSignMachine::sign` causes denial of service - (File: crypto/frost/src/sign.rs)

### Summary
The signing-set validation in `AlgorithmSignMachine::sign` only bounds participant indexes from above (`> n`) and rejects duplicates; it never rejects `Participant(0)` (or any index outside the valid `1..=n` set). The `included` list is then passed to `ThresholdKeys::view`, whose `Option` result is unwrapped unconditionally. An attacker who supplies a preprocess map keyed under an invalid participant index can force an `unwrap()` panic, crashing the signer — an unauthenticated, remotely reachable denial of service analogous to the "protection mechanism failure → denial of service" class of CVE-2022-36289.

### Finding Description
`sign` builds `included` from `self` plus every key of the attacker-supplied `preprocesses` map, sorts it, and applies three checks (`crypto/frost/src/sign.rs:290-313`):

- `included.len() < t` → "not enough signers"
- `included.last() > n` → `InvalidParticipant`
- adjacent equal elements → `DuplicatedParticipant`

There is no lower-bound check: `Participant(0)` (and any `Participant` not corresponding to a real signer but `<= n`, e.g. an index never issued by the DKG) passes all validation. The list is then used in:

```rust
let view = self.params.keys.view(included.clone()).unwrap();
```

`ThresholdKeys::view` returns an `Option`, and computes a `ThresholdView` over the included participants — interpolating verification shares via Lagrange coefficients. For `Participant(0)` (or any index with no verification share), `view` cannot produce a valid view, and the unconditional `unwrap()` panics. `validate_map` (called right after) only checks that the preprocess map keys match `included`, so it does not catch the invalid index either — the panic is reached before or at `view`.

### Impact Explanation
Any party able to submit a preprocess/signature-share map to a FROST `AlgorithmSignMachine` (i.e., any participant in a signing session, or anything feeding attacker-controlled bytes into `read_preprocess` + `sign`) can crash the signing process by including a preprocess under `Participant(0)` or another non-issued index `<= n`. This is a reliable, single-message denial of service against each signer that processes the malicious preprocess set — matching the Medium-severity DoS impact class of the reference CVE.

### Likelihood Explanation
The trigger requires only control of the `preprocesses` `HashMap` keys, which in any deployment are populated from deserialized peer messages (`read_preprocess`). No threshold of colluders, no valid shares, and no cryptographic work is needed — a single malformed participant index in an otherwise well-formed message suffices. The only mitigation is that the crash is a panic (caught if the host wraps signing in `catch_unwind`), and I could not fully verify `ThresholdKeys::view`'s exact rejection behavior for index 0 since `crypto/frost/src/keys.rs` was not inspected in this analysis; if `view` tolerates the index rather than returning `None`, the invalid participant still flows into `B.insert`, `Lagrange` interpolation, and `verify_share`, likely producing a panic or an incorrect-view state downstream instead.

### Recommendation
Validate that every element of `included` satisfies `1 <= i <= n` (reject `Participant(0)` explicitly) before calling `view`, mirroring the existing `> n` check, e.g.:

```rust
if (u16::from(included[0]) == 0) ||
   (u16::from(included[included.len() - 1]) > multisig_params.n()) {
  Err(FrostError::InvalidParticipant(...))?;
}
```

Additionally, replace the `unwrap()` on `keys.view(included)` with a propagated `FrostError::InvalidSigningSet`/`InternalError` so that even an unanticipated invalid set cannot panic the caller.

### Proof of Concept
```rust
// Participant i runs a normal preprocess, then calls sign with a
// preprocess map that additionally contains Participant(0):
let mut preprocesses = HashMap::new();
// ... insert valid preprocesses from t-1 real peers ...
// Attacker-controlled extra entry:
preprocesses.insert(
  Participant(0),
  sign_machine.read_preprocess(&mut &attacker_bytes[..]).unwrap(),
);
// included becomes [0, i, ...]; passes all checks (0 <= n, no dups),
// then panics:
sign_machine.sign(preprocesses, b"msg"); // unwrap() on None inside view()
```

The panic occurs at `crypto/frost/src/sign.rs:312` (`self.params.keys.view(included.clone()).unwrap()`), reachable entirely through untrusted preprocess data with no prior authentication failure.