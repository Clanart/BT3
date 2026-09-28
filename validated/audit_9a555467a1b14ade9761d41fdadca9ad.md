### Title
Panic via unbounded `Participant` index in blame evaluation causes denial of service - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2023-0190 is a NULL-pointer-dereference → denial-of-service in a kernel-mode driver. The Rust analog is an unchecked-index panic reachable on untrusted input. `AdditionalBlameMachine::blame` / `BlameMachine::blame` accept `sender` and `recipient` as raw `Participant` values (any non-zero `u16`) and never validate them against the registered participant set before indexing `HashMap`s with them. Two indexing sites panic on a missing key:

- `self.enc_keys[&decryptor]` in `Decryption::decrypt_with_proof`, crypto/dkg/pedpop/src/encryption.rs (line 388), reached whenever a blame proof (`Some(EncryptionKeyProof)`) accompanies an accusation whose `recipient` was never registered in `AdditionalBlameMachine::new` (which only inserts keys for `1..=n`, lib.rs:656-659).
- `self.commitments[&sender]` in `BlameMachine::blame_internal`, crypto/dkg/pedpop/src/lib.rs (line 599), reached when the provided `EncryptionKeyProof` verifies correctly (or `proof` is `None` and the PoP signature check fails — that path returns early, but once `decrypt_with_proof` returns `Ok` or `InvalidProof`, an out-of-range `sender` hits the `commitments` index).

`Participant::new` only rejects `0` (crypto/dkg/src/lib.rs:29-35), so e.g. `Participant(0xFFFF)` is a validly-constructed index that is absent from the maps. `blame` takes an `EncryptedMessage` that arrives via the explicitly in-scope `EncryptedMessage::read` path (encryption.rs:171-177).

### Impact Explanation
Any call to `blame`/`blame_internal` with a `recipient` ∉ {1..=n} and a `Some(proof)`, or a `sender` ∉ {1..=n} whose message passes `decrypt_with_proof`, panics the calling thread. In a validator/processor context where accusations are transported as protocol messages, a party able to submit an accusation (or to be named in one) with an out-of-range index crashes the blame-handling path — an abort/panic DoS, matching the CVE's "NULL dereference → denial of service" class. The panic also permanently wedges the `BlameMachine` state machine (it is consumed / its `result` is never returned).

### Likelihood Explanation
The trigger requires only a `Participant` value outside `1..=n` plus a blame message routed to `blame`. Whether an integrator bounds-checks accuser/accused indexes before calling is outside the library; the library itself performs no validation — `AdditionalBlameMachine::new` even documents that invalid input "may cause ... panics" (lib.rs:648), but that warning covers *invalid commitment messages*, not the `blame` arguments, which are the untrusted runtime inputs. A malicious accuser controlling the `recipient` (their own claimed index) or the `sender` field can reach it. Medium likelihood: requires a protocol that forwards attacker-influenced participant indexes into `blame`, and the attacker must additionally get a message past `decrypt_with_proof` for the `sender` path.

### Recommendation
Validate `sender` and `recipient` against the registered participant set at the top of `blame_internal` (e.g., `self.commitments.contains_key(&sender)` and `self.enc_keys.contains_key(&decryptor)`), returning the accusing party (or an error) instead of indexing. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `get(...)` plus an explicit fault decision.

### Proof of Concept
```rust
// crypto/dkg/pedpop context; C = Ristretto (or any Ciphersuite)
// After a normal DKG with params n = 5, build an AdditionalBlameMachine:
let machine = AdditionalBlameMachine::<C>::new(context, 5, commitment_msgs).unwrap();

// Craft an EncryptedMessage via EncryptedMessage::read (in-scope parse path)
// with a valid PoP for `sender`, then call blame with an out-of-range index:
let bogus_recipient = Participant::new(0xFFFF).unwrap(); // valid Participant, not in 1..=5
// Path 1: proof = Some(valid EncryptionKeyProof)
//   -> decrypt_with_proof indexes self.enc_keys[&bogus_recipient]
//   -> HashMap Index panics (encryption.rs:388)
machine.blame(sender, bogus_recipient, msg, Some(proof));

// Path 2: bogus sender whose msg survives decrypt_with_proof
//   -> blame_internal indexes self.commitments[&bogus_sender] (lib.rs:599)
//   -> panic
```

Both paths are unconditional `HashMap` index panics on values never checked against `n`, satisfying the NULL-deref→DoS analog on Serai's own code.