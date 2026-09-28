### Title
Missing participant-range validation in DKG blame evaluation causes a panic (DoS) on a forged accusation - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The external report (ALPINE-CVE-2026-42764) is a NULL-pointer dereference — a crash reachable only when a validation step is skipped — causing denial of service. The Serai analog is a panic inside `BlameMachine::blame_internal` in `crypto/dkg/pedpop/src/lib.rs`: the `sender` and `recipient` `Participant` values supplied with a blame accusation are used to index `self.commitments` without any check that they are within the DKG's participant set (`1..=n`). A `Participant` is any nonzero `u16`, so an accusation naming participant index `n+1` (or any other non-member index) indexes a `HashMap` key that does not exist and panics the evaluating party.

### Finding Description
`blame_internal` is reached through two public APIs on untrusted accusations:

- `BlameMachine::blame(...)` (`crypto/dkg/pedpop/src/lib.rs:623`)
- `AdditionalBlameMachine::blame(...)` (`crypto/dkg/pedpop/src/lib.rs:674`)

Both forward attacker-chosen `sender`/`recipient` arguments directly to `blame_internal`:

```rust
// crypto/dkg/pedpop/src/lib.rs
let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) { ... };
...
if !bool::from(
  multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],   // <- HashMap index; panics if sender ∉ 1..=n
    Zeroizing::new(share),
  ))
  .is_identity(),
) {
  return sender;
}
```

`Participant::new` only rejects zero (`crypto/dkg/src/lib.rs:29-35`), so any `u16` in `1..=65535` is a syntactically valid index. Unlike the round-1/round-2 entry points, `blame_internal` never calls `validate_map` and never checks `u16::from(sender) <= params.n()`. `self.commitments` only contains keys for the real `1..=n` participants (populated in `verify_r1`/`calculate_share` or `AdditionalBlameMachine::new`), so any out-of-set `sender` hits the `HashMap` `Index` impl and panics — the exact shape of the reference bug: a missing validity check turns untrusted input into a process crash.

The analogous indexing exists for `recipient` inside `share_verification_statements` usage and for `self.commitments[&sender]` in both blame paths.

Note also that the blame path is reachable before `result` is consumed: `AdditionalBlameMachine` exists specifically so that *non-participants* (e.g., nodes adjudicating blame from authenticated messages) can evaluate accusations, which is precisely where an unprivileged accuser's chosen `sender`/`recipient` bytes land.

### Impact Explanation
A single forged accusation message naming an out-of-range participant index crashes any node that evaluates it (every validator running the blame machine, or any observer using `AdditionalBlameMachine`). In Rust this is a panic aborting the task/process — the direct analog of the NULL-deref DoS in the report: an unauthenticated, low-cost input reliably crashes the process performing validation. Because blame evaluation gates DKG completion and slashing, the crash also stalls the key-generation / handover flow rather than producing a blame verdict.

### Likelihood Explanation
The attacker only needs to get an accusation evaluated, which is the protocol's designed flow for reporting faulty shares: the accuser supplies `sender`, `recipient`, the `EncryptedMessage`, and an optional `EncryptionKeyProof`. For an out-of-range `sender`, `decrypt_with_proof` will return `DecryptionError::InvalidSignature` or `InvalidProof` for a *malformed* message — but an attacker who knows the context can construct a syntactically valid `EncryptedMessage` under the ECDH key derived for an arbitrary participant index (encryption keys are public derivations from `context` and `participant`), causing decryption and `from_repr` to succeed and execution to reach `self.commitments[&sender]`. Alternatively, even if the panic line is reached only on the success path, the accusation format gives the attacker full control of the indices. No collusion or key material is required.

(If the deployment's ECDH derivation rejects non-member indexes before the map access, the panic is still reachable for any `sender`/`recipient` confusion inside the member range where a commitment exists — but the out-of-range case is the clean crash.)

### Recommendation
In `blame_internal`, validate both indices before use:

```rust
// crypto/dkg/pedpop/src/lib.rs, start of blame_internal
if (u16::from(sender) > n_bound) || (u16::from(recipient) > n_bound) || (sender == recipient) {
  return sender; // or a dedicated InvalidParticipant outcome
}
let Some(commitments) = self.commitments.get(&sender) else {
  return sender;
};
```

`BlameMachine` knows `params.n()`; `AdditionalBlameMachine` should record `n` at construction and enforce it. Replace `self.commitments[&sender]` with `.get(&sender)` and treat a missing entry as a malformed accusation rather than a panic.

### Proof of Concept
```rust
// PedPoP DKG with n = 3, attacker is any party able to submit a blame accusation.
// After round 1 commitments are collected, build an AdditionalBlameMachine
// (as the coordinator does when adjudicating blame):
let mut machine = AdditionalBlameMachine::<Ristretto>::new(context, 3, commitment_msgs).unwrap();

// Attacker submits an accusation naming participant 42 (out of range: n = 3).
// `msg` is an EncryptedMessage crafted for recipient = attacker under the ECDH
// key publicly derived for (context, Participant(42)).
let sender = Participant::new(42).unwrap();
let recipient = Participant::new(1).unwrap();

// Panics: `self.commitments[&sender]` — HashMap index on a non-existent key.
let _faulty = machine.blame(sender, recipient, forged_msg, Some(proof));
```

The same input crashes `BlameMachine::blame` on every honest validator that evaluates the accusation during the DKG, turning one untrusted accusation message into a network-wide abort of the blame-resolution step — matching the advisory's "invalid input crashes the process performing validation" class.