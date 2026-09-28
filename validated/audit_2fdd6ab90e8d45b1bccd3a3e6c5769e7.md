### Title
Unvalidated participant indexes in PedPoP blame evaluation cause an index-out-of-map panic, crashing the evaluating party - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2016-9799 is a crash on malformed input: `pklg_read_hci` trusts bytes from an untrusted dump file and crashes `btmon`. The analog in Serai is PedPoP's blame path, which indexes `HashMap`s keyed by `Participant` using attacker-controlled participant indexes without ever validating them against `1 ..= n`. `Participant::new` accepts any non-zero `u16`, so a blame accusation naming a non-participant `sender`/`recipient` causes `self.commitments[&sender]` / `self.enc_keys[&decryptor]` to panic, aborting the process evaluating blame.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept `sender: Participant` and `recipient: Participant` directly from an accusation and forward them to `blame_internal`:

- `blame_internal` calls `decrypt_with_proof`, which does `self.enc_keys[&decryptor]` — a `HashMap` index that panics if `decryptor` was never registered (i.e., `recipient` is not a DKG participant) — crypto/dkg/pedpop/src/encryption.rs:388.
- If `proof` is `None`, `blame_internal` still reaches `self.commitments[&sender]` — again a `HashMap` index that panics for any `sender` index not in the DKG — crypto/dkg/pedpop/src/lib.rs:599.

Neither `blame` entry point checks `u16::from(sender) <= n` or `u16::from(recipient) <= n`. The only constructors that populate `commitments`/`enc_keys` are `calculate_share` (which fills exactly `1 ..= n` via `all_participant_indexes`) and `AdditionalBlameMachine::new` (same range, crypto/dkg/pedpop/src/lib.rs:656). Any index `> n` — a perfectly valid `Participant` since `Participant::new` only rejects `0` (crypto/dkg/src/lib.rs:29-35) — reaches a `HashMap` `Index` impl that panics on a missing key.

This is directly reachable: an accuser's claim (`sender`, `recipient`, plus the accused `EncryptedMessage`) is exactly the untrusted public input `blame` exists to adjudicate, and `AdditionalBlameMachine::blame` is explicitly designed to evaluate accusations on behalf of third parties. The panic propagates as an abort of whoever evaluates the accusation — same availability-only impact class as the CVE (corrupted input → crash).

### Impact Explanation
Any party able to submit a blame accusation naming an out-of-range participant index can panic the PedPoP blame-evaluation path on an honest node. Since `blame` consumes `self` only on the success path and the panic occurs inside `blame_internal`, the evaluating process dies instead of returning a blame verdict. In a validator/coordinator context this is a remotely triggerable crash of DKG adjudication — a liveness failure that can be repeated per accusation, blocking DKG completion/slashing evaluation.

### Likelihood Explanation
The trigger is a single `Participant` value `> n` (or any index absent from `commitments`/`enc_keys`) supplied as `sender` or `recipient` to `blame`. No valid proof or signature is needed: with `proof: None` the code panics on `enc_keys[&decryptor]` before any cryptographic check, and even with a valid proof the `commitments[&sender]` index panics. The only requirement is that the host evaluates accusations containing attacker-chosen indices without pre-filtering them to `1 ..= n` — which the API neither enforces nor documents.

### Recommendation
Validate `sender` and `recipient` in `blame`/`blame_internal` before indexing: return a defined fault (or an `Err`) when either index is not present in `commitments`/`enc_keys` (equivalently, when `u16::from(i) > n`). Replace `map[&k]` indexing with `map.get(&k)` plus an error path, and bound-check indexes in `AdditionalBlameMachine::new`/`blame` against the `n` used at construction.

### Proof of Concept
```rust
// Participant indexes are only constrained to be non-zero:
let bad_sender = Participant::new(n + 1).unwrap();   // valid Participant, not a DKG member
let bad_recipient = Participant::new(n + 2).unwrap();

// On any BlameMachine / AdditionalBlameMachine built for n participants:
// 1) Panics inside decrypt_with_proof on `self.enc_keys[&decryptor]`
//    (crypto/dkg/pedpop/src/encryption.rs:388)
machine.blame(sender_p, bad_recipient, msg, Some(proof));

// 2) Panics inside blame_internal on `self.commitments[&sender]`
//    (crypto/dkg/pedpop/src/lib.rs:599)
machine.blame(bad_sender, recipient_p, msg, None);
```
Both calls panic via `HashMap`'s `Index` impl because `Participant::new` admits any non-zero `u16` and `blame` performs no `1 ..= n` membership check before indexing.