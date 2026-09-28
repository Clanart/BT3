### Title
Out-of-range participant indexes in PedPoP blame evaluation cause an unauthenticated panic/DoS - (File: crypto/dkg/pedpop/src/encryption.rs + crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to CVE-2025-21501 (a low-privileged attacker with network access causes a repeatable crash/DoS), `AdditionalBlameMachine::blame` / `BlameMachine::blame` index `HashMap`s with attacker-supplied `Participant` values that were never range-checked, causing a deterministic panic. The blame path is explicitly designed to be callable by a third party who was not a DKG participant, so this is reachable purely from public inputs.

### Finding Description
`AdditionalBlameMachine::new` registers encryption keys and commitments only for participant indexes `1 ..= n` (`crypto/dkg/pedpop/src/lib.rs:656-660`). However, `blame`/`blame_internal` accept `sender` and `recipient` `Participant` values and use them unchecked as map keys:

- `self.enc_keys[&decryptor]` inside `Decryption::decrypt_with_proof` (`crypto/dkg/pedpop/src/encryption.rs:388`) — evaluated as an argument to `dleq.verify` whenever `proof` is `Some`, *before* any proof validity check.
- `self.commitments[&sender]` in `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:599`), reached whenever decryption with the supplied proof succeeds.

There is no validation that `sender <= n` or `recipient <= n` (nor that either differs from / is in the registered set). Any `Participant` in `1 ..= u16::MAX` is structurally valid. Supplying e.g. `recipient = n + 1` with any `Some(proof)` panics on `enc_keys[&decryptor]`; supplying `sender = n + 1` with a decryptable message panics on `commitments[&sender]`.

### Impact Explanation
A panic in this code path aborts the calling task/process (Serai binaries install a panic hook that exits the process). Since blame verification is triggered by messages any participant (or even a non-participant observer, per the documented purpose of `AdditionalBlameMachine`) can submit with attacker-chosen `accuser`/`accused` indexes, an unprivileged party can repeatedly crash the node evaluating blame — a complete, repeatable availability loss with no key material required. This matches the CVE-2025-21501 shape: low-privilege, network-reachable, availability-only impact (CVSS 6.5 analog: Medium).

### Likelihood Explanation
Reachability requires only: (1) a populated `AdditionalBlameMachine`/`BlameMachine`, and (2) an accusation message whose `sender`/`recipient` fields lie outside `1 ..= n`. For the `enc_keys[&decryptor]` panic, the caller additionally needs `proof: Some(..)` and a `msg.pop` that verifies — but `msg.key`/`msg.pop` are attacker-chosen, so a valid PoP is trivially produced (pick any scalar `k`, set `key = k*G`, sign). For `BlameMachine::blame`, `sender > n` panics after a successful decrypt, which is also fully attacker-controlled since the encrypted message is attacker-supplied bytes via `EncryptedMessage::read`.

### Recommendation
Bounds-check `sender`/`recipient` in `blame_internal` (and document/return an error) before indexing: return a defined `PedPoPError` (e.g. `InvalidParticipant`/`MissingParticipant`) if either index is not present in `self.commitments`/`self.encryption.enc_keys`, rather than relying on `HashMap` indexing which panics. Use `get()` with an explicit error instead of `[&k]` at `encryption.rs:388` and `lib.rs:599`.

### Proof of Concept
```rust
// Setup: DKG params with n participants; build the third-party blame machine
let params = ThresholdParams::new(t, n, own_i).unwrap();
let machine = AdditionalBlameMachine::<Ristretto>::new(context, n, commitment_msgs).unwrap();

// Attacker supplies an out-of-range recipient (accused) index
let evil_recipient = Participant::new(n + 1).unwrap();

// Craft a syntactically valid EncryptedMessage with a self-made PoP
let k = <Ristretto as Ciphersuite>::random_nonzero_F(&mut rng);
let key = <Ristretto as Ciphersuite>::generator() * k;
let nonce = <Ristretto as Ciphersuite>::generator() * r;
let pop = SchnorrSignature::sign(&k, r, pop_challenge(context, nonce, key, sender, &msg_bytes));

// proof = Some(garbage-or-valid EncryptionKeyProof) suffices: enc_keys[&n+1]
// is indexed as an argument before the DLEq is verified -> panic
machine.blame(sender, evil_recipient, msg, Some(proof)); // panics at encryption.rs:388
```

Uncertainty note: I verified the unchecked indexing and the `1 ..= n` registration in the in-scope PedPoP code, but did not fully trace the upstream processor call site (`VerifyBlame`) to confirm it forwards unvalidated `accuser`/`accused` values; the panic stands on `pedpop`'s public API surface regardless, since `blame` accepts arbitrary `Participant` values from public inputs.