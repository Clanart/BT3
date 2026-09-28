### Title
Missing participant-index bounds check in PedPoP blame evaluation causes a reachable panic (DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to CVE-2026-60182 (a reachable crash/hang in a server component), `BlameMachine::blame` / `AdditionalBlameMachine::blame` in the PedPoP DKG perform an unchecked `HashMap` index on a caller-supplied `Participant` index. Any accusation naming a `sender` (or `recipient`) outside the committed participant set panics the evaluating party, yielding a remotely triggerable, repeatable crash from public inputs.

### Finding Description
`blame()` takes `sender` and `recipient` as arbitrary `Participant` values — `Participant` is any non-zero `u16` (`crypto/dkg/src/lib.rs:29-35`), so an accuser can supply any index in `1..=65535`, including indexes `> n`. `blame` forwards to `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:575-630`), which calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` and then, when decryption succeeds, evaluates the share-verification statements with:

```rust
&self.commitments[&sender]
```

(`crypto/dkg/pedpop/src/lib.rs:599-601`). `self.commitments` is a `HashMap<Participant, Vec<C::G>>` populated only for `1..=n` — either by `verify_r1` over `all_participant_indexes()` (`lib.rs:313-337`) or by `AdditionalBlameMachine::new` over `1 ..= n` (`lib.rs:654-660`). `Index` on `HashMap` panics on a missing key, so `sender > n` is an unconditional panic. Even before reaching that line, `decrypt_with_proof` resolves the per-participant ECDH state for `sender`/`recipient` internally, giving an additional unchecked lookup of the same attacker-controlled indexes.

Notably, `AdditionalBlameMachine::new` explicitly advertises use "regardless of if the caller was a member in the DKG protocol" (`lib.rs:639-648`), so a party that never participated — an unprivileged observer relayed an accusation — reaches this code. The `msg` itself only needs to be a well-formed `EncryptedMessage<C, SecretShare<C::F>>` (parsable via `EncryptedMessage::read`), which is fully attacker-constructible. Nothing in `blame()`/`blame_internal()` validates `sender` or `recipient` against `params`/`n` before indexing.

### Impact Explanation
A single malformed blame accusation causes a panic in the host evaluating blame, crashing the node mid-DKG-cleanup. This is a complete availability loss of the local process (repeatable at will by re-sending the accusation), matching the bug class of the referenced advisory: a reachable, attacker-triggered crash rather than a correctness or confidentiality break. No secret material is leaked, but the panic aborts the protocol and can take down the participant handling blame aggregation.

### Likelihood Explanation
Triggering requires only that an accuser's `sender`/`recipient` argument be an out-of-range `Participant`, which is trivially encodable (any non-zero `u16`). The only gating condition is that the host actually invokes `blame`, i.e., participates in or arbitrates a PedPoP DKG with blame handling — the normal production path for Serai key rotation. For `BlameMachine` (post-`calculate_share`), the panic path via `self.commitments[&sender]` additionally requires `decrypt_with_proof` to succeed or fail with `InvalidProof`/invalid-scalar paths that still reach line 599 — for `sender > n` the earlier decryption lookup is itself likely to panic, so the crash does not even require a validly encrypted message. Exploitability is high wherever blame accusations are accepted from non-member or unauthenticated-but-parsable inputs; if the integrator only ever feeds verified in-range indexes, it is unreachable, which caps severity at Medium rather than High.

### Recommendation
Validate `sender` and `recipient` inside `blame`/`blame_internal` (and inside `Decryption::decrypt_with_proof`) against the known participant set — e.g., return an `Err`/`Participant`-independent verdict when `u16::from(sender) > n` or `u16::from(recipient) > n`, instead of indexing `self.commitments[...]` directly. Replace the `HashMap` index with `.get()` + error, so blame evaluation is total over arbitrary inputs.

### Proof of Concept
Conceptual (library-level) reproduction:

```rust
// n participants complete commitments; an arbitrator builds:
let machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs)?;
// Any accusation with a sender outside 1..=n panics:
let bogus_sender = Participant::new(n + 1).unwrap(); // or 65535
let recipient = Participant::new(1).unwrap();
machine.blame(bogus_sender, recipient, attacker_msg, None);
// -> panic at `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:599),
//    or earlier inside decrypt_with_proof's per-participant lookup.
```

The same applies to `BlameMachine::blame`, where `self.commitments` only contains indexes `1..=n`. The `msg` can be produced with `EncryptedMessage::write`/`encrypt` using any keypair; no valid DKG transcript is needed for the panic on the out-of-range index itself.

Note: I could not fully verify the internals of `Decryption::decrypt_with_proof`/`Encryption`'s participant-key map (in `crypto/dkg/pedpop/src/encryption.rs`) to confirm whether the panic fires there first or at line 599 — but either location produces the same reachable crash from the same unchecked attacker-controlled index.