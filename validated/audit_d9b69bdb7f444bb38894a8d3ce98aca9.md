### Title
Untrusted accuser index in PedPoP blame verification panics via `HashMap` index on `enc_keys` - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` (encryption.rs:388), where `decryptor` is the accusing participant. `enc_keys` is only populated by `register` for participants who actually submitted commitment messages. When a blame verification is invoked with an `accuser` `Participant` value that was never registered (or lies outside the signing set), the `HashMap` index panics, aborting the blame-verification path — the Serai analog of CVE-2019-14891's shared-cgroup kill: an untrusted input (a workload-like participant index) terminates the management process (blame verification) rather than being cleanly rejected.

### Finding Description
`Encryption::decrypt` queues the sender's per-message PoP into a shared `BatchVerifier` and produces an `EncryptionKeyProof`; on failure, `KeyMachine::calculate_share` maps `BatchId::Decryption(l)` to an error with `blame: None` (crypto/dkg/pedpop/src/lib.rs:493-499). The recipient then escalates via a `BlameMachine`/`AdditionalBlameMachine` path that calls `Decryption::decrypt_with_proof(from, decryptor, msg, proof)`. Inside `decrypt_with_proof` (encryption.rs:366-397), the DLEq is verified against `self.enc_keys[&decryptor]` with no membership check and no `get`-style error handling — `HashMap`'s `Index` impl panics on a missing key. `enc_keys` is populated exclusively in `Decryption::register` (encryption.rs:351-362) for the concrete participants of `verify_r1`, so any `Participant` index supplied for `decryptor` that wasn't a registered commitment sender reaches an out-of-bounds lookup. The processor-level blame flow (`CoordinatorMessage::VerifyBlame`, processor/src/key_gen.rs:504+) passes an `accuser` participant value derived from an incoming message into `blame(...)`, which is the entry point reaching this code.

### Impact Explanation
A participant (or any party able to submit a blame/VerifyBlame message naming an arbitrary `Participant` index as accuser) can cause a panic in the blame-verification routine of every party that processes it. Panicking the coordinator/processor thread aborts DKG completion and blame adjudication — mirroring the CVE's effect of a workload fault killing the container-management path. This yields a denial of service against the key-generation/blame protocol from a single malformed public input, with no secret-material prerequisites for the attacker. Because panic unwinding occurs before `Ok`/`Err` is returned, callers cannot distinguish it from success and no blame is produced — a faulty party can also evade attribution by crashing the verifier.

### Likelihood Explanation
The reachable precondition is a `decryptor`/`accuser` participant index not present in `enc_keys`. `enc_keys` contains exactly the participants who registered commitment messages in `verify_r1` (lib.rs:313-331). Any index outside that set — e.g., a `Participant` value ≤ n that simply didn't send commitments, or an index never in the session — triggers the panic. Whether upstream `AdditionalBlameMachine::blame` validates `accuser` against the session set could not be fully confirmed within this scan; if it does not (the function signature accepts a bare `Participant` with no evident bounds check visible in the reviewed code), the panic is directly reachable by a single participant sending a VerifyBlame naming an unregistered accuser. Even with upstream validation of `accuser ∈ 1..=n`, a valid-but-non-registered index still panics, since registration requires an actual commitment message.

### Recommendation
Replace `self.enc_keys[&decryptor]` in `decrypt_with_proof` (encryption.rs:388) with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` so an unregistered accuser produces a typed error instead of a panic. Additionally, validate `accuser`/`decryptor` membership in the session participant set at the `BlameMachine`/`AdditionalBlameMachine::blame` entry point and return `PedPoPError::MissingParticipant`/`InvalidCommitments`-style errors for out-of-set indexes.

### Proof of Concept
Conceptual (no test harness executed):

```rust
// A participant completes round 1 with peers {1,2,3}; enc_keys holds {1,2,3}.
// Attacker submits a blame request naming accuser = Participant(7)
// (or any index that never registered a commitment message).
// In Decryption::decrypt_with_proof:
proof.dleq.verify(
  &mut encryption_key_transcript(self.context),
  &[C::generator(), msg.key],
  &[self.enc_keys[&decryptor], *proof.key], // panics: key 7 absent
)
```

Expected: thread panic at encryption.rs:388 (`HashMap` index out of bounds) instead of `Err(DecryptionError::InvalidProof)`, killing the blame-verification path — functionally equivalent to the OOM-kill-of-conmon condition in CVE-2019-14891, where one party's input terminates the shared management process.