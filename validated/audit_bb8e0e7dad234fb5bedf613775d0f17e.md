### Title
Missing-participant lookup panics in PedPoP blame path, crashing the node - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept caller-supplied `sender`/`recipient` `Participant` indexes and index `self.commitments` (and the ECDH key store inside `decrypt_with_proof`) with `map[&participant]`, which panics when the accused `sender`/`recipient` was never registered in the commitments map — e.g., a `Participant` index `> n` or any index absent from the map. Analogous to CVE-2023-37034 (an S1AP message missing the expected `TAI` field dereferences a null pointer and crashes the MME), an attacker can submit a blame/accusation naming a participant index that is not present, causing a panic that aborts the process instead of a clean error.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs`, `blame_internal` resolves the accused sender's commitments by direct `HashMap` indexing:

- Line 599: `&self.commitments[&sender]` inside `share_verification_statements` — panics on missing key.
- `AdditionalBlameMachine::new` (lines 656–660) only inserts `Participant` indexes `1..= n` into `commitments`, so any `sender`/`recipient` outside that range is guaranteed absent.
- `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` (line 582) is invoked before the share check; it resolves the per-participant ECDH encryption key for `sender`, which is keyed the same way — so a missing `sender` entry either panics there or, if decryption fails early as `InvalidSignature`/`InvalidProof`, a validly formed message attributed to a nonexistent `recipient`/`sender` still reaches line 599 and panics.

`Participant::new` (crypto/dkg/src/lib.rs, lines 29–35) only enforces nonzero, so `Participant(n + k)` for any `k` is a perfectly constructible, serializable value. Nothing in `blame`/`blame_internal`/`AdditionalBlameMachine::blame` validates `sender <= n` or `recipient <= n` or checks `commitments.contains_key` before indexing. The docs (lines 645–648) even acknowledge this path "may cause everything from inaccurate blame to panics" — confirming the reachable panic exists rather than a handled error.

Note: the same `validate_map`-protected paths elsewhere (`verify_r1` line 305, `calculate_share` line 468, `sign()`'s `included` checks at crypto/frost/src/sign.rs lines 298–310) do bound and deduplicate participant sets; the blame interface is the one entry point where attacker-influenced indexes flow straight into `HashMap` indexing.

### Impact Explanation
An unprivileged party that can trigger a blame evaluation (submitting an accusation of a faulty DKG share, or invoking `AdditionalBlameMachine::blame` with crafted `sender`/`recipient`) causes an unconditional panic in the host process. This is a direct availability loss — the Rust analog of the CVE's null-pointer crash — killing the signing/key-gen node mid-protocol. Since blame is evaluated when adjudicating faults, an attacker being legitimately accused can also weaponize the path to crash the arbiter, preventing fault attribution and forcing DKG aborts.

### Likelihood Explanation
Medium. Triggering requires only choosing an out-of-range `Participant` index on a blame call — no cryptographic work, no valid share, no collusion. It does require the deployment to route accusation data into `blame`/`AdditionalBlameMachine::blame` with attacker-influenced `sender`/`recipient` arguments (the library explicitly delegates message authentication to the caller), and the impact is a crash/DoS rather than key or signature compromise. That matches the CVE's Medium (6.5) profile.

### Recommendation
- Bound-check `sender` and `recipient` in `BlameMachine::blame`, `AdditionalBlameMachine::blame`, and `blame_internal` (reject `u16::from(p) > params.n()` / `> n` or `!commitments.contains_key(&p)`) and return the accusing party as faulty or a `PedPoPError` instead of panicking.
- Replace `self.commitments[&sender]` / `self.commitments[&recipient]` indexing (line 599) with `.get()` + handled error.
- Apply the same check inside `Decryption::decrypt_with_proof` before resolving the per-participant ECDH key.

### Proof of Concept
```rust
// crypto/dkg/pedpop — conceptual PoC
// Setup: honest DKG with n = 3 produces a BlameMachine / AdditionalBlameMachine.
// `commitments` then contains exactly Participants {1, 2, 3}.

let bogus = Participant::new(200).unwrap(); // valid Participant, but > n

// Any EncryptedMessage; blame() indexes sender's ECDH key / commitments
// before or regardless of the msg contents.
let _faulty = machine.blame(
    bogus,        // sender not present in commitments
    honest_i,     // recipient
    msg,          // EncryptedMessage<C, SecretShare<C::F>> (any bytes parse)
    proof,        // Option<EncryptionKeyProof<C>>
);
// Panics at crypto/dkg/pedpop/src/lib.rs:599 on `self.commitments[&sender]`
// (or earlier inside decrypt_with_proof resolving the ECDH key) —
// index-out-of-map panic => process abort, exactly the missing-field crash
// class of CVE-2023-37034.
```

Uncertainty note: `decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs) may panic even earlier when resolving the sender's registered key, or may return `DecryptionError::InvalidProof` — in the latter case the panic still occurs at line 599 for a `sender`/`recipient` absent from `commitments`, since no bounds check exists on that path either.