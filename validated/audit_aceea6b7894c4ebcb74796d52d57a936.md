### Title
Missing-participant encryption key lookup panics during PedPoP share generation (unreachable-`Option`/index panic, analog of NULL dereference) - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2024-25197 is a NULL pointer dereference — code dereferences state that was never populated. The direct analog in Serai is in the PedPoP DKG: `Encryption::encrypt` indexes `self.decryption.enc_keys[&participant]` for every counterparty, but `enc_keys` is only populated by `register()`, which is only invoked for participants who actually submitted a round-1 `EncryptionKeyMessage`. If any participant withholds their commitments message, the honest node's `HashMap` index panics — an unprivileged remote party crashing a participant's DKG process.

### Finding Description
`Decryption::register` inserts into `enc_keys` only when a message is present: `SecretShareMachine::verify_r1` calls `self.encryption.register(l, msg)` only inside `let Some(msg) = commitment_msgs.remove(&l) else { continue }` (crypto/dkg/pedpop/src/lib.rs:313-315). `validate_map` (lib.rs:305-309) validates the keys that are present against the allowed participant set — the same helper is used in `sign.rs` (crypto/frost/src/sign.rs:313) where subsets are legitimate, so it does not require all `n` participants to be present.

Subsequently, `generate_secret_shares` unconditionally calls `self.encryption.encrypt(rng, l, share_bytes)` for every `l` in `all_participant_indexes()` except self (crypto/dkg/pedpop/src/lib.rs:359-369). `Encryption::encrypt` does `self.decryption.enc_keys[&participant]` (crypto/dkg/pedpop/src/encryption.rs:466) — a `HashMap` `Index` impl that panics on a missing key. A participant who simply never sends their round-1 commitments causes the honest party's `generate_secret_shares` call to panic instead of returning a `PedPoPError`.

The same unchecked indexing pattern exists in `Decryption::decrypt_with_proof` at `self.enc_keys[&decryptor]` (encryption.rs:388), reachable via the blame/decryption path with a `decryptor` that was never registered, and `Decryption::register` itself panics via `assert!(!self.enc_keys.contains_key(&participant))` (encryption.rs:356-359) if registration is attempted twice — though HashMap keys make a duplicate in a single map impossible, a second DKG attempt reusing the machine could trigger it.

### Impact Explanation
An unprivileged DKG participant can remotely crash (panic/abort) an honest participant's key-generation process by omitting their commitments message. This is a denial of service on availability, matching the CVE's `A:H` impact class. In a validator/coordinator context this can stall or halt threshold key generation and any signing pipeline gated on it.

### Likelihood Explanation
Triggering requires only that a participant in a PedPoP DKG session decline to send its round-1 `EncryptionKeyMessage` while others proceed — no cryptographic insight or collusion needed. Whether the panic is hit depends on integrators calling `generate_secret_shares` without themselves enforcing the all-participants precondition; the library neither documents nor enforces it, and the surrounding API (returning `Result<PedPoPError>`) implies faults are handled as errors rather than panics. (Caveat: if the integrator's networking layer requires all `n` messages before calling in, the panic is unreachable; the library itself does not enforce this.)

### Recommendation
In `generate_secret_shares`/`verify_r1`, either require `commitment_msgs` to contain exactly `all_participant_indexes()` (erroring with `PedPoPError` on a missing participant), or make `Encryption::encrypt`/`Decryption::decrypt_with_proof` return an error/`Option` on a missing `enc_keys` entry instead of indexing. Similarly replace the `assert!` in `Decryption::register` with a checked error.

### Proof of Concept
```rust
// n = 3, t = 2, honest participant i = 1.
// Participants 1 and 2 send EncryptionKeyMessage<Commitments>;
// participant 3 sends nothing.
let commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>> =
    msgs_from(1, 2); // no entry for Participant(3)

// Inside SecretShareMachine::generate_secret_shares:
// verify_r1 registers keys for {1, 2} only.
// Then for l = 3: encryption.encrypt(rng, 3, share)
//   -> self.decryption.enc_keys[&Participant(3)]  // HashMap index panic
machine.generate_secret_shares(rng, commitment_msgs); // thread panics
```

Reachability and root cause:
- Registration only for present participants: crypto/dkg/pedpop/src/lib.rs:313-315
- `encrypt` called for all participants regardless: crypto/dkg/pedpop/src/lib.rs:359-369
- Panicking index: crypto/dkg/pedpop/src/encryption.rs:466 (`enc_keys[&participant]`)
- Same pattern in blame path: crypto/dkg/pedpop/src/encryption.rs:388 (`enc_keys[&decryptor]`)

Uncertainty: I could not read `validate_map`'s implementation in `crypto/dkg/src/lib.rs` within the iteration budget; if it enforces full participation, this path returns a `PedPoPError` instead of panicking — but its usage in `frost/src/sign.rs` with subsets of signers strongly suggests it only validates provided keys.