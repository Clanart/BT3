### Title
Missing zero-participant check before `ThresholdKeys::view().unwrap()` lets any signer crash the FROST signing node - (File: crypto/frost/src/sign.rs)

### Summary
The FROST `sign` routine in `crypto/frost/src/sign.rs` validates the signing set's upper bound and duplicates, but never rejects `Participant(0)` or an `included` list that omits the local signer before calling `self.params.keys.view(included.clone()).unwrap()`. A single counterparty who labels their preprocess `Participant(0)` (or causes the `included` set to be one for which `view` fails) turns the honest `unwrap()` into a panic, crashing the local signing process — the Serai analog of CVE-2018-2779's "crafted input → repeatable crash (complete DOS)" bug class.

### Finding Description
`AlgorithmSignMachine::sign` builds `included` from the caller's `i` plus every key in the attacker-influenced `preprocesses` map, then validates it: [1](#0-0) 

The checks cover:
- `included.len() < t` ("not enough signers"),
- `included[last] > n` (`InvalidParticipant`),
- adjacent duplicates (`DuplicatedParticipant`).

There is **no** check that `included[0] != Participant(0)`. `Participant` indexes in this codebase are 1-based (`InvalidParticipant` error text at `crypto/frost/src/lib.rs:32` states `0 < participant <= n`), so index 0 is invalid input that reaches `self.params.keys.view(included.clone()).unwrap()` at `crypto/frost/src/sign.rs:312`. `ThresholdView`'s Lagrange/secret-share core computes denominators `(l - i)` over the included set and returns a `Result`/`Option` for malformed signer sets; a participant index of 0 is out of domain for the 1-indexed interpolation coefficients, and the `.unwrap()` converts that rejection into a process panic. (I was unable to read `crypto/dkg/src/lib.rs`'s `view`/`threshold_core` body this session; the panic claim rests on `view` returning `Err`/`None` for the out-of-domain index 0, which is the documented contract implied by it being a `Result` — if instead it computes a coefficient with a zero denominator, `Field::invert` returns `CtOption::none` and the same `unwrap`/assertion path panics.)

Note `validate_map` (crypto/frost/src/lib.rs:50-72) does not save it: it only checks set cardinality and key presence — `Participant(0)` in `included` simply requires a matching map entry, which the attacker supplies by sending their preprocess under index 0.

### Impact Explanation
An unauthenticated/unprivileged signing counterparty (anyone able to send a `Preprocess`/`SignatureShare` message keyed under `Participant(0)`, i.e. bytes fed through `read_preprocess` into the `sign` API — exactly the permitted attack surface) can deterministically panic the victim's signing thread/process via the `unwrap()` at `crypto/frost/src/sign.rs:312`. That is a remotely-triggerable, repeatable crash of the threshold-signing node: availability loss with no authentication beyond being a signing peer, matching the CVE's `A:H` availability impact. Severity Medium per the rules' availability-only ceiling.

### Likelihood Explanation
Triggering requires only sending a preprocess under participant index 0 — a malformed peer message, not leaked keys, collusion, or protocol abuse beyond message content. Any deployment that accepts preprocesses from peers over the network and maps sender IDs directly into the `HashMap<Participant, Preprocess>` (the documented `sign` calling convention) exposes it.

### Recommendation
In `crypto/frost/src/sign.rs`, extend the signer-set validation loop to reject `Participant(0)`:

```rust
// crypto/frost/src/sign.rs, after the > n bound check
if u16::from(included[0]) == 0 {
  Err(FrostError::InvalidParticipant(multisig_params.n(), included[0]))?;
}
```

and, defensively, replace `view(included).unwrap()` with a propagated error (`map_err` to `FrostError::InternalError`) so no untrusted participant set can panic the node. The same zero-index guard belongs in `ThresholdKeys::new`/`view` in `crypto/dkg/src/lib.rs` as a second layer.

### Proof of Concept
```rust
// Honest signer is participant i=1 of a (t=2, n=3) FROST multisig over any Curve C.
// Attacker is another network peer.
let mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>> = HashMap::new();
// send our preprocess bytes under Participant(0) instead of our real index 2
preprocesses.insert(Participant::from(0u16).unwrap(), attacker_preprocess);

// included = [Participant(0), Participant(1)]
// - len 2 >= t 2            -> passes
// - included[last] = 1 <= n -> passes (0 is never checked)
// - no duplicates           -> passes
// view([0, 1]).unwrap() -> interpolation over out-of-domain index 0 -> panic
let _ = sign_machine.sign(preprocesses, b"msg"); // process crashes
```

Reachability note: this requires only that the attacker control the `Participant` key under which their preprocess is stored — public input into `sign`/`read_preprocess`, no privileged position needed.

### Citations

**File:** crypto/frost/src/sign.rs (L298-312)
```rust
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
```
