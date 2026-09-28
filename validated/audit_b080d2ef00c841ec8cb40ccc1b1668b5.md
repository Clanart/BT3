### Title
Missing lower-bound participant check causes panic (`unwrap`/missing HashMap key) in FROST `sign`, crashing the signer - (File: crypto/frost/src/sign.rs)

### Summary
`AlgorithmSignMachine::sign` validates the `included` signing set for the upper bound (`> n`) and for duplicates, but never checks the lower bound (`Participant(0)`). It then calls `self.params.keys.view(included).unwrap()`, and `ThresholdView` construction looks up per-participant verification shares in a map keyed `1..=n`. A `Participant(0)` in the preprocess map therefore makes `view()` fail (or indexing the verification-share map panic), turning an unprivileged peer's malformed preprocess entry into a panic that crashes the signing validator. This is the Serai analog of CVE-2018-2646: an input-validation gap reachable over the protocol path that yields a repeatable, remotely triggerable crash (complete DoS of that node's signing).

### Finding Description
In `crypto/frost/src/sign.rs`, `sign` builds `included` from the local participant plus every key in the caller-supplied `HashMap<Participant, Preprocess>`, sorts it, and performs exactly three checks:

- `included.len() < t` → error (line 298)
- `included[included.len() - 1] > n` → error (line 302, max element only)
- `included[i] == included[i+1]` → error (lines 306-310)

It never rejects `included[0] == Participant(0)`. Execution then reaches `self.params.keys.view(included.clone()).unwrap()` (line 312). `view` resolves a verification share for each included participant against the `verification_shares` map, which is populated only for `1..=n` (see `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`, lines 620-623, `(1 ..= n).map(Participant)`). `Participant(0)` has no verification share, so `view` returns `Err` (or panics inside share lookup) and the `unwrap()` at line 312 panics.

`Participant` is a public tuple struct (`Participant(u16)`), so index `0` is constructible — the codebase itself builds participants directly in `crypto/dkg/src/lib.rs`. Every other participant-derived bound is enforced in `sign` except the `i >= 1` invariant, even though `Participant::new` (used everywhere bytes are parsed, e.g. `crypto/dkg/src/lib.rs:600` and the tributary `Signed`/DKG paths) exists precisely to reject `0`. Any caller that keys the `commitments`/`preprocesses` map by a participant index influenced by a peer — exactly what the FROST preprocess API is for (`read_preprocess` reads the value bytes; the caller supplies the key) — lets a malicious participant claim index `0` and panic every honest signer that processes it.

### Impact Explanation
Each victim running the FROST `sign` path (including the Bitcoin `TransactionSignMachine::sign`, which delegates to `AlgorithmSignMachine::sign` per input in `networks/bitcoin/src/wallet/send.rs` lines 383-391) panics rather than returning a `FrostError`. In Rust a panic unwinds/abort-crashes the thread; repeated triggering gives a "hang or frequently repeatable crash (complete DoS)" identical in effect to the advisory. The attacker only needs to introduce one malformed preprocess entry — the panic occurs before any nonce/share material is consumed.

### Likelihood Explanation
Medium. The bug requires a signing session in which a `Participant(0)` key reaches `sign`. All length/count and upper-bound checks are already implemented, suggesting the authors intended full validation here and simply omitted the `>= 1` check; whether a given deployment lets a peer-influenced index of `0` reach the map depends on the caller. Unlike the documented "negligible probability" identity-point panics in `networks/bitcoin/src/crypto.rs`, this panic is deterministically triggerable once the malformed index arrives, and it is not excluded as a malicious-validator case: crashing *honest* validators via malformed messages is a standard reachable DoS, not a validator-maliciousness assumption.

### Recommendation
In `AlgorithmSignMachine::sign` (`crypto/frost/src/sign.rs`), reject `included[0] == Participant(0)` alongside the existing `> n` and duplicate checks (e.g., `if u16::from(included[0]) == 0 { Err(FrostError::InvalidParticipant(0, included[0]))?; }`), and replace `view(included).unwrap()` with a propagated error so no deserialization/participant inconsistency can panic. Apply the same lower-bound validation anywhere `ThresholdView`/`view` is built from externally influenced participant sets.

### Proof of Concept
```rust
// Any ThresholdKeys<Secp256k1> with t >= 2, our participant i = 1
let (sign_machine, _preprocess) = AlgorithmMachine::new(Schnorr::new(), keys.clone())
    .preprocess(&mut rng);

let mut preprocesses = HashMap::new();
// Peer-supplied preprocess entry keyed by the invalid Participant(0)
preprocesses.insert(
    Participant(0), // never rejected by sign(); Participant is a pub tuple struct
    malicious_preprocess_bytes_parsed_via_read_preprocess,
);
// plus enough honest preprocesses to satisfy `included.len() >= t`

// included == [0, 1, ...] -> passes <t, >n, and dup checks
// -> keys.view(included).unwrap() panics at crypto/frost/src/sign.rs:312
let _ = sign_machine.sign(preprocesses, b"msg"); // PANIC: repeatable crash
```

Note: I verified the validation gap and the `view().unwrap()` panic site directly in `crypto/frost/src/sign.rs` (lines 290-312) and the `1..=n` verification-share keying in `crypto/dkg/src/lib.rs` (lines 620-623). The one element I could not fully confirm within the available scope is whether `keys.view()` returns `Err` on the missing `Participant(0)` share or panics internally on the HashMap index — either way the outcome is a panic/crash of the caller rather than a handled `FrostError`.