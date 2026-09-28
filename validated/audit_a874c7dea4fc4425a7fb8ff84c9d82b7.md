### Title
Out-of-range participant indexes in blame evaluation panic the process (remote DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The `blame`/`blame_internal` path in PedPoP indexes `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with caller-supplied `Participant` values that are never checked against `1 ..= n`. A blame message naming a recipient (or sender) that was never registered causes a `HashMap` index panic, crashing the caller — the direct analog of CVE-2025-50101's remotely-triggered crash/hang availability impact.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept `sender` and `recipient` as raw `Participant` values (a non-zero `u16`, `Participant::new` only rejects zero) and pass them to `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:575-609`). `blame_internal` calls `Decryption::decrypt_with_proof`, which — once the attacker-supplied `msg.pop` Schnorr proof verifies — evaluates `self.enc_keys[&decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:388`) to build the DLEq verification pair. `enc_keys` only contains entries for participants `1 ..= n` registered during round 1, so any `recipient > n` panics. Likewise, `self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`) panics for any sender outside the committed set. `Participant` indexes embedded in blame traffic are attacker-chosen, and the PoP signature is verified against `msg.key`, a point the attacker fully controls, so satisfying the pre-panic check is trivial.

### Impact Explanation
An unauthenticated-at-the-crypto-layer party can submit a blame accusation (the data carried by an `InvalidDkgShare`-style flow) naming a non-existent `recipient` index (e.g., `Participant(65535)`) together with a self-signed `EncryptionKeyProof`. Every honest node that runs `blame()` to adjudicate the accusation panics inside `HashMap` indexing and, in deployments that abort on task panic, the process dies — a complete availability loss matching the upstream CVE's "unauthorized ability to cause a hang or frequently repeatable crash." No valid share, key material, or collusion is required; the crash occurs before blame is adjudicated and repeatedly on retry.

### Likelihood Explanation
The trigger requires only: (a) a syntactically valid `EncryptedMessage` whose `pop` verifies under the attacker's own `msg.key` for a claimed `sender` — trivially constructible with `SchnorrSignature::sign`; (b) `proof: Some(_)` with any structurally-parseable `EncryptionKeyProof` (the DLEq is never reached before the panic, since `self.enc_keys[&decryptor]` is evaluated while building the `verify` arguments); and (c) a `recipient` index outside `1 ..= n`. `AdditionalBlameMachine::new` documents that inputs must be authenticated, yet nothing validates that the `sender`/`recipient` arguments to `blame` are within the registered set — validation of the *accusation's fields* is the library's job and is missing.

### Recommendation
In `blame_internal` (and `Decryption::decrypt_with_proof`), validate `sender` and `recipient` membership before indexing: return a defined `PedPoPError`/`DecryptionError` (or treat an unregistered index as the accuser being faulty) instead of `self.commitments[&sender]` / `self.enc_keys[&decryptor]`. Use `.get(&participant)` with an explicit error path, and range-check both `Participant` values against `params`/registered sets at the `blame` entry points.

### Proof of Concept
```rust
// Assume a completed PedPoP DKG among n = 3 participants and that the
// coordinator/node holds an `AdditionalBlameMachine` built via
// AdditionalBlameMachine::new(context, 3, commitment_msgs).

// Attacker constructs a syntactically valid EncryptedMessage for any claimed
// `sender`, because `pop` is verified against the attacker-controlled `msg.key`:
let key = Zeroizing::new(<C as Ciphersuite>::random_nonzero_F(&mut rng));
let pub_key = C::generator() * key.deref();
let nonce = Zeroizing::new(C::random_nonzero_F(&mut rng));
let pub_nonce = C::generator() * nonce.deref();
let msg = SecretShare::<C::F>(<C::F as PrimeField>::Repr::default()); // arbitrary bytes
let pop = SchnorrSignature::<C>::sign(
    &key, nonce,
    pop_challenge::<C>(context, pub_nonce, pub_key, sender_index, msg.as_ref()),
);
let evil_msg = EncryptedMessage { key: pub_key, pop, msg: Zeroizing::new(msg) };

// Attacker supplies any structurally-parseable proof; it is never verified
// because the panic happens while assembling the verify arguments.
let proof = Some(EncryptionKeyProof::<C>::read(&mut crafted_bytes.as_ref()).unwrap());

// `recipient` names a non-existent participant.
machine.blame(sender_index, Participant::new(65535).unwrap(), evil_msg, proof);
// -> decrypt_with_proof evaluates `self.enc_keys[&decryptor]` at
//    crypto/dkg/pedpop/src/encryption.rs:388 -> HashMap index panic -> crash.
```

A symmetric panic occurs at `self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`) when the accused `sender` is an index outside the committed participant set and the preceding checks are satisfied.

Note on confidence: the panic sites and the missing range check are directly evidenced in the files read; the end-to-end reachability assumes the integrator routes network-supplied `accuser`/`faulty` participant indexes into `blame` unvalidated, which is the documented usage ("the message should be a copy of the encrypted secret share ... must have been authenticated as actually having come from the sender" — authenticity of the *sender's index* is not something the library can check itself, and the crash occurs regardless of that authentication for the `recipient` field).