### Title
Unauthenticated panic via out-of-range participant index in DKG blame decryption - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
Analogous to the OPA parser bug (CWE-119, denial of service via out-of-range access on untrusted input), `Decryption::decrypt_with_proof` performs an unchecked `HashMap` index on `self.enc_keys[&decryptor]` — in Rust this is an out-of-bounds-equivalent access that panics. The `decryptor` index is attacker-controlled: it is the `recipient` argument passed to `BlameMachine::blame` / `AdditionalBlameMachine::blame`, both of which take a `Participant` chosen by the accusing party over the (unauthenticated-by-design) blame channel.

### Finding Description
`Decryption::register` populates `enc_keys` only for the participants `1..=n` of the DKG (`crypto/dkg/pedpop/src/encryption.rs` lines 356-360). In `decrypt_with_proof`, after the (correctly validated) Schnorr PoP check, the code dereferences `self.enc_keys[&decryptor]` to build the DLEq verification points:

```rust
// crypto/dkg/pedpop/src/encryption.rs:381-390
proof
  .dleq
  .verify(
    &mut encryption_key_transcript(self.context),
    &[C::generator(), msg.key],
    &[self.enc_keys[&decryptor], *proof.key],
  )
  .map_err(|_| DecryptionError::InvalidProof)?;
```

There is no membership check: `HashMap::index` panics when the key is absent. Reachability is through `BlameMachine::blame` → `blame_internal` (`crypto/dkg/pedpop/src/lib.rs` lines 582-588) and through the explicitly public `AdditionalBlameMachine::blame` (lines 674-682), which is documented as usable by parties who "was [not] a member in the DKG protocol" (line 639). Both accept `recipient: Participant` verbatim and pass it as `decryptor` without checking `recipient <= n`. `Participant::new` accepts any nonzero `u16` up to 65535, so any accuser can name a `recipient` outside `1..=n` (e.g. `Participant::new(n + 1)`).

Note the same panic does NOT require the PoP to verify — the panic happens after `msg.pop.verify` succeeds, but an accuser can satisfy that by generating a fresh key/PoP themselves (`pop_challenge` binds only `context`, `nonce`, `key`, `sender`, `msg` — all chosen by the accuser; see `crypto/dkg/pedpop/src/encryption.rs` lines 302-324). A valid `EncryptionKeyProof` with an arbitrary DLEq can also be produced by the accuser for their own keys.

### Impact Explanation
Any node evaluating a blame accusation — a DKG participant via `BlameMachine::blame`, or a third party/validator via `AdditionalBlameMachine::blame` — crashes (panic/abort) when the accusation names a `recipient` outside the registered participant set. This is the same outcome class as the reference bug: an unprivileged party supplying out-of-range input causes denial of service of the host application. In a validator set where blame evaluation is done on-chain or deterministically by all honest nodes, one crafted accusation can halt the key-rotation/recovery flow for every evaluator simultaneously.

### Likelihood Explanation
Reachability requires only that the attacker can submit a blame accusation (authenticated as themselves, with the alleged `sender`/`recipient` labels of their choosing). No threshold collusion, leaked keys, or malicious-integrator assumptions are needed. The defect is a missing bounds/membership check on a parameter that is unambiguously attacker-controlled.

### Recommendation
In `Decryption::decrypt_with_proof`, replace `self.enc_keys[&decryptor]` with a fallible lookup (`self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?`), and/or validate `sender`/`recipient` against `enc_keys`/known participants at the top of `blame_internal` and `AdditionalBlameMachine::blame` before any map indexing.

### Proof of Concept
```rust
// Given a completed PedPoP DKG with params (t, n, i), or an
// AdditionalBlameMachine built from the n commitment messages:
let out_of_range = Participant::new(n + 1).unwrap(); // valid Participant, not in 1..=n

// Attacker crafts any EncryptedMessage with a self-consistent PoP
// (encrypt() with the attacker's own enc_key registration, or a fresh
// key + SchnorrSignature over pop_challenge for `from = sender`).
let msg: EncryptedMessage<C, SecretShare<C::F>> = attacker_msg;
let proof: Option<EncryptionKeyProof<C>> = attacker_proof;

// The following panics at crypto/dkg/pedpop/src/encryption.rs:388 on
// `self.enc_keys[&decryptor]` because `out_of_range` was never registered:
let faulty = additional_blame_machine.blame(sender, out_of_range, msg, proof);
```

Caveat: this is a panic-based DoS analog (safe-Rust equivalent of the reference's OOB access), not memory corruption; confidence in reachability is high since `blame`/`blame_internal` perform no `recipient` bounds check, though I was unable to inspect `crypto/dkg/musig` or `networks/bitcoin/src/crypto.rs`/`wallet/send.rs` for potentially stronger analogs in this pass.