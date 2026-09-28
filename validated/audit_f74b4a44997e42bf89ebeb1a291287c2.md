### Title
Unconditional lookup of an encryption key that was never registered causes a panic when evaluating blame proofs - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2021-47453 freed an IDA unconditionally in a teardown path regardless of whether it was allocated, crashing when the resource was absent. The Serai analog is in PedPoP's blame path: `Decryption::decrypt_with_proof` unconditionally indexes `self.enc_keys[&decryptor]` (encryption.rs:388) and `BlameMachine::blame_internal` unconditionally indexes `self.commitments[&sender]` (lib.rs:599), even though the `enc_keys` map is only populated for participants other than the local one, and `AdditionalBlameMachine` accepts arbitrary `Participant` indexes that may never have been registered.

### Finding Description
`Encryption::register` / `Decryption::register` inserts `msg.enc_key` into `enc_keys` keyed by participant (encryption.rs:351-362). In `SecretShareMachine::verify_r1`, `register` is only invoked for participants whose commitment message is present in `commitment_msgs`, and `validate_map` requires that map to exclude the local participant `i` (lib.rs:305-315). Consequently, `enc_keys` never contains an entry for the local participant `i`, nor for any index outside `1..=n`.

`BlameMachine::blame_internal` calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` (lib.rs:582). When `proof` is `Some` — which is exactly the case where the accusing recipient proves it decrypted a share — the code evaluates `self.enc_keys[&decryptor]` to build the DLEq verification generators (encryption.rs:388). For the normal `BlameMachine` flow the `recipient`/decryptor is the accuser, i.e., the local participant `i`, whose key was never registered, so any blame claim accompanied by an `EncryptionKeyProof` panics on the `HashMap` index. Additionally, `AdditionalBlameMachine::blame` accepts caller-supplied `sender`/`recipient` indexes; any index `> n` (or `0` is impossible, but any unregistered index) panics either at `enc_keys[&decryptor]` or at `self.commitments[&sender]` (lib.rs:599), since `AdditionalBlameMachine::new` only registers `1..=n` (lib.rs:656-660).

The `msg` and `proof` arguments are attacker-controlled bytes parseable via `EncryptedMessage::read` and `EncryptionKeyProof::read`, and the `sender`/`recipient` arguments come from an unauthenticated accusation, so this is reachable entirely from public inputs.

### Impact Explanation
A participant (or any party invoking `AdditionalBlameMachine::blame`) that submits a blame proof — including a fully legitimate one — crashes the process instead of getting a blame verdict. An unauthenticated accuser can also deliberately supply an out-of-range `recipient`/`sender` index to crash any node evaluating blame. This halts the DKG abort-and-blame procedure, preventing identification of the faulty party and denial of service of key generation. This mirrors the original CVE's availability impact (crash on a path that touches a resource assumed allocated).

### Likelihood Explanation
The panic triggers on the happy path of blame evaluation: any accusation backed by an `EncryptionKeyProof` where the accuser's own participant index was not registered hits `enc_keys[&decryptor]`. Since self-registration is structurally impossible (the local key lives in `enc_key`, not in `enc_keys`), and arbitrary indexes are accepted by `AdditionalBlameMachine`, the crash is deterministic whenever triggered.

### Recommendation
Replace the direct `HashMap` indexing in `decrypt_with_proof` (encryption.rs:388) and `blame_internal` (lib.rs:599) with checked lookups (e.g., `enc_keys.get(&decryptor).ok_or(DecryptionError::...)` / `commitments.get(&sender)`), or explicitly record the local participant's public encryption key in `enc_keys` during `Encryption::new` so the map is total over `1..=n`. `AdditionalBlameMachine::blame` should also validate `sender`/`recipient` against the known participant set before dereferencing internal maps.

### Proof of Concept
1. Run a PedPoP DKG to `KeyMachine` stage; `verify_r1` registers `enc_keys` only for other participants.
2. Call `KeyMachine::calculate_share` obtaining a `BlameMachine`.
3. Invoke `BlameMachine::blame(sender = l, recipient = i, msg = <valid EncryptedMessage>, proof = Some(<valid EncryptionKeyProof>))` — the honest accusation format.
4. `decrypt_with_proof` reaches `self.enc_keys[&i]`, which was never inserted → panic (`HashMap` index out of bounds).
5. Alternatively, construct `AdditionalBlameMachine::new(context, n, msgs)` and call `blame(sender = Participant::new(n+1), recipient = any, msg, proof)`; `self.commitments[&sender]` panics.