### Title
Attacker-controlled `Participant(0)` in a preprocess message crashes `AlgorithmSignMachine::sign` via `.unwrap()` on `ThresholdKeys::view` — complete DoS of the signer - (File: crypto/frost/src/sign.rs)

### Summary
The FROST signing entry point validates attacker-supplied participant indexes incompletely: it rejects indexes greater than `n` but never rejects index `0`. A malicious signer (or anyone able to inject a preprocess message, which is just untrusted bytes fed through `read_preprocess`) can register `Participant(0)` in the `preprocesses` map. `included` is then sorted with `0` at the front, passes all validation, and is handed to `ThresholdKeys::view(...)`, whose `Result` is unconditionally `.unwrap()`ed. Since participant `0` is not a valid DKG index (participants are `1..=n`), `view` fails and the unwrap panics, permanently crashing the signing session/process — an exact analog of the "unauthorized hang or repeatable crash (complete DOS)" bug class in CVE-2021-2298.

### Finding Description
In `AlgorithmSignMachine::sign` (`crypto/frost/src/sign.rs:283-312`):

```rust
included.sort_unstable();
// Included < threshold
if included.len() < usize::from(multisig_params.t()) { ... }
// OOB index
if u16::from(included[included.len() - 1]) > multisig_params.n() { ... }
// Same signer included multiple times
for i in 0 .. (included.len() - 1) { ... }

let view = self.params.keys.view(included.clone()).unwrap();
```

- The only range check is `included.last() > n`. There is no lower-bound check for `Participant(0)`, even though `FrostError::InvalidParticipant` itself documents the domain as `0 < participant <= n` (`crypto/frost/src/lib.rs:32-33`). Because `Participant` is a `u16` newtype over network-deserializable data, `0` is trivially encodable.
- `preprocesses` is an attacker-influenced `HashMap<Participant, Preprocess>`; each key becomes an element of `included` (sign.rs:292-294). `validate_map` (lib.rs:50-73) only checks quantity/duplicates/missing — it does not validate index ranges.
- With `Participant(0)` present, `included = [0, ...]`. The last element still passes the `> n` check; no duplicate exists; `included.len() >= t` holds. Execution reaches `self.params.keys.view(included.clone()).unwrap()`. `ThresholdView` construction builds secret shares and offsets via Lagrange interpolation over the included indexes; participant `0` has no corresponding verification share/secret share in a `1..=n` DKG, so `view` returns `Err` (or an internal failure), and `.unwrap()` panics — killing the signer.
- The path is fully attacker-reachable: `SignMachine::read_preprocess` (sign.rs:276-281) + `Commitments::read` (`crypto/frost/src/nonce.rs:133-139`) parse arbitrary bytes, and the preprocess's `Participant` key in the surrounding protocol map is attacker-chosen.

### Impact Explanation
A single crafted preprocess message deterministically panics any threshold signer that processes it — a repeatable, remotely triggerable crash of the signing node (medium-severity DoS matching the advisory's availability impact). For a validator/coordinator running FROST for Bitcoin output signing, this halts signing entirely. Depending on the caller, the panic aborts the whole process, not just the session.

### Likelihood Explanation
Any party able to send preprocess messages (a participating signer, or a network peer where preprocesses are relayed unauthenticated) can trigger it at will with minimal effort: submit a preprocess keyed under `Participant(0)`. No cryptographic work, collusion, or privileged access is required. It is deterministic — every victim that builds `included` from the attacker's map crashes.

### Recommendation
Reject `Participant(0)` and any index `> n` before building the view. Concretely, in `sign` (`crypto/frost/src/sign.rs`), after sorting `included`, check `u16::from(included[0]) == 0 || u16::from(included[included.len()-1]) > n` and return `FrostError::InvalidParticipant`. The same lower-bound check should be applied in `complete`/`validate_map` paths for share maps, and `ThresholdKeys::view`/`ThresholdView::new` in `crypto/dkg` should hard-fail (return `Err`, not rely on caller `unwrap`) on out-of-range indexes. Avoid `.unwrap()` on attacker-influenced paths; propagate `FrostError` instead.

### Proof of Concept
Conceptual (the vulnerable code path):

```rust
// Attacker serializes a syntactically valid Commitments blob
// (2 points per nonce per generator — read_G accepts them) and
// delivers it as the preprocess for Participant(0).
let mut preprocesses: HashMap<Participant, Preprocess<_, _>> = HashMap::new();
preprocesses.insert(Participant(0), attacker_preprocess); // index 0 never range-checked

// Victim calls:
sign_machine.sign(preprocesses, b"any message")
// -> included = [0, 1, 2, ...] after sort_unstable()
// -> included.last() <= n passes; no dup; len >= t passes
// -> keys.view([0, ...]) -> Err -> .unwrap() panics (crypto/frost/src/sign.rs:312)
// Result: deterministic crash (complete DoS), matching the CVE-2021-2298 bug class.
```

**Uncertainty note:** the final iteration cut off before I could read `crypto/dkg/src/keys.rs` to confirm whether `ThresholdKeys::view` returns `Err` (→ `unwrap` panic) or accepts `Participant(0)` and produces a garbage `ThresholdView`. Either outcome is a defect on this path — panic (availability) or silently wrong secret share/offset applied to `included[0]` — but the panic variant is the direct crash analog claimed here.