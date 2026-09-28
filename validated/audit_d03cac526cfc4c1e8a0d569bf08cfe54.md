### Title
`AdditionalBlameMachine::blame` panics on out-of-range `Participant` indexes via HashMap indexing of `enc_keys`/`commitments` with attacker-controlled `sender`/`recipient` - (File: crypto/dkg/pedpop/src/encryption.rs:388)

### Summary
The bug class of the external report — attacker-controlled inputs reaching unchecked indexing and crashing the process (cookie/CIDR handling corrupting or crashing Apache Traffic Server) — maps onto PedPoP's blame-verification path. `BlameMachine::blame` / `AdditionalBlameMachine::blame` take `sender` and `recipient` as raw `Participant` values and index `self.enc_keys[&decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:388`) and `self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`) with `HashMap`'s `Index` impl, which panics on a missing key. `Participant` is only checked to be non-zero (`Participant::new`); nothing bounds `sender`/`recipient` to `1 ..= n`, while both maps only ever contain keys `1 ..= n`. Any `Participant` value `> n` therefore reaches a `HashMap` index panic.

### Finding Description
- `AdditionalBlameMachine::new` populates `commitments`/`enc_keys` strictly for `i in 1 ..= n` (`crypto/dkg/pedpop/src/lib.rs:656-660`).
- `blame()` forwards `sender`, `recipient` (the accused and accuser, both attacker-chosen in a blame message) to `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:630`, `681`).
- `blame_internal` calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` (`crypto/dkg/pedpop/src/lib.rs:582`). Inside `decrypt_with_proof`, after verifying `msg.pop`, if a `proof` is supplied the code evaluates `self.enc_keys[&decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:381-389`). `decryptor` is `recipient` = the accuser's `Participant` — attacker-controlled.
- If `decrypt_with_proof` succeeds (it cannot with an out-of-range `sender` as the `enc_keys` panic fires first for an out-of-range `recipient`), `blame_internal` then does `&self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:596-600`) — the same unchecked index on `sender` = the accused.
- This is reachable by an unprivileged party: `EncryptedMessage::read` and `EncryptionKeyProof::read` only parse bytes (`crypto/dkg/pedpop/src/encryption.rs:171-177`, `267-269`), and the `pop` Schnorr "proof of possession" is computed over attacker-chosen `from` bytes (`pop_challenge`, `crypto/dkg/pedpop/src/encryption.rs:302-324`), so anyone can craft a `msg` whose `pop` verifies for an arbitrary `sender` index. In Serai, this flows from `CoordinatorMessage::VerifyBlame { accuser, accused, share, blame }` → `EncryptedMessage::read` → `AdditionalBlameMachine::blame(accuser, accused, ...)` (`processor/src/key_gen.rs:504-549`), where `accuser`/`accused` come straight from the submitted transaction — mirroring how the ATS bug is triggered by attacker-supplied header/cookie bytes.

### Impact Explanation
A remote, unauthenticated participant index of `> n` in a blame accusation causes a guaranteed panic (`HashMap` index out of bounds / missing-key panic) inside the key-confirmation/blame arbitration path, crashing the processor/coordinator handler thread. This is a remotely-triggered denial of service on the DKG finalization path — the direct Rust analog of the CVE's crash/memory-corruption-on-untrusted-input behavior (CVSS VA:H). Because blame verification is required to complete key generation, a crashed processor also stalls or aborts the DKG for the session.

### Likelihood Explanation
Triggering requires only submitting a `VerifyBlame`-shaped message (or equivalent blame flow) with `accuser` or `accused` set to a valid nonzero `Participant` index greater than `n`, plus a self-signed `pop` and any parseable `EncryptionKeyProof`. No secret knowledge, collusion, or valid DKG share is needed — the panic fires on the map index before the DLEq proof or share value is meaningfully checked. `Participant` deliberately permits any nonzero `u16`, so indexes up to 65535 are accepted regardless of `n`.

### Recommendation
Validate `sender` and `recipient` against `params`/the `commitments`/`enc_keys` key sets at the top of `blame_internal` (or in `blame`/`AdditionalBlameMachine::blame`) and return a defined error — e.g., treat an out-of-range accuser as faulty — instead of indexing. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `get(...)` + error propagation so malformed participant indexes cannot panic.

### Proof of Concept
```
// n = 7, context fixed for the DKG session.
// Attacker crafts blame where the accuser index is out of range:
let accuser  = Participant::new(8).unwrap(); // > n
let accused  = Participant::new(1).unwrap();

// msg: EncryptedMessage with a self-signed pop binding `from = accused`.
// key = k*G for known k; pop = SchnorrSignature::sign(k, r, pop_challenge(ctx, rG, kG, accused, msg_bytes))
let msg = EncryptedMessage::<Ristretto, SecretShare<Scalar>>::read(&mut bytes, params).unwrap();

// Any parseable proof suffices to reach the indexing line:
let proof = Some(EncryptionKeyProof::read(&mut proof_bytes).unwrap());

// blame_internal -> decrypt_with_proof -> self.enc_keys[&accuser]
// enc_keys only holds keys 1..=7 -> HashMap index panic (process crash)
machine.blame(accuser, accused, msg, proof);
```
Panic sites: `self.enc_keys[&decryptor]` at `crypto/dkg/pedpop/src/encryption.rs:388` (fires when `recipient`/accuser > n, provided `proof.is_some()` and `msg.pop` verifies — both attacker-satisfiable) and `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599` (fires when `sender`/accused > n and decryption succeeded).