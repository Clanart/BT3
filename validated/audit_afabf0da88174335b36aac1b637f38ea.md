### Title
Out-of-range `Participant` indices in a blame accusation panic `BlameMachine::blame` / `AdditionalBlameMachine::blame` instead of attributing fault (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to CVE-2023-24822 (crafted input reaching a code path that dereferences a nonexistent object → hard fault / DoS), Serai's PedPoP blame-evaluation code indexes `HashMap`s keyed by `Participant` without validating that the attacker-influenced `sender`/`recipient` indices are members of the DKG set. Any `Participant` value outside `1..=n` causes an index panic (Rust's equivalent of the null dereference) inside `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal`, crashing the host instead of returning a fault attribution.

### Finding Description
`AdditionalBlameMachine::new` / `BlameMachine` build two maps containing only legitimate participants `1..=n`:

- `Decryption.enc_keys: HashMap<Participant, C::G>`, populated in `AdditionalBlameMachine::new` only for `Participant::new(i)` with `i in 1..=n` (`crypto/dkg/pedpop/src/lib.rs:656-660`).
- `BlameMachine.commitments: HashMap<Participant, Vec<C::G>>`, same range (`lib.rs:654-661`).

`blame()` then forwards caller-supplied `sender` and `recipient` `Participant`s into `blame_internal`, which performs two unchecked map index operations on untrusted indices:

1. `self.enc_keys[&decryptor]` inside `Decryption::decrypt_with_proof` at `crypto/dkg/pedpop/src/encryption.rs:388`. This is evaluated as a function argument *before* `DLEqProof::verify` runs, so it panics whenever `decryptor` (the accusing `recipient`) is not in `1..=n`, regardless of proof validity. It is reached as soon as the attacker supplies a `msg` whose Schnorr PoP verifies — `pop_challenge` binds `sender` (`from`), and the accuser controls the ephemeral `key`/`pop`, so this is trivially satisfiable — plus any non-`None` `proof`.
2. `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599`, reached after a successful `decrypt_with_proof`. When `sender` (the accused) is out of range but `recipient` is a real participant (who knows their own ECDH scalar and can therefore produce a valid `EncryptionKeyProof`), decryption succeeds, the share parses, and this index panics.

`Participant::new` rejects only zero (`crypto/dkg/src/lib.rs:600`); `Transaction::read` decodes `accuser`/`faulty` as arbitrary nonzero `u16` (`coordinator/src/tributary/transaction.rs:346-353`) and never range-checks them against the session's `n`. The processor passes these values straight into `.blame(accuser, accused, ...)` (`processor/src/key_gen.rs:543-556`), so a serialized `InvalidDkgShare`/blame transaction carrying an index `> n` deterministically panics every processor that evaluates it — aborting the blame path and taking down the node process, the same availability impact class as the RIOT hard fault.

### Impact Explanation
A single crafted blame accusation containing an out-of-range `Participant` deterministically panics each processor node that runs `BlameMachine::blame`/`AdditionalBlameMachine::blame`. This is a remote denial of service of the DKG fault-attribution/slashing path (and of the processor itself, which handles the message synchronously and uses `unwrap()`/`panic!` semantics), matching the CVE-2023-24822 availability-impact class.

### Likelihood Explanation
Triggering requires submitting a blame/accusation flow message with an out-of-range `accuser` or `accused` index plus a well-formed `EncryptedMessage` PoP (freely craftable) and, for the `commitments[&sender]` variant, a valid `EncryptionKeyProof` (producible by any real participant, who knows their encryption scalar). The in-protocol reachability depends on whether the coordinator accepts `InvalidDkgShare` transactions signed only by set members; the library-level panic itself is unconditional given the out-of-range `Participant` inputs, and `AdditionalBlameMachine` is explicitly documented as usable by non-participants evaluating others' blame — a usage where the caller receives these indices over the wire.

### Recommendation
Range-check `sender` and `recipient` against the registered participants before indexing: in `BlameMachine::blame`/`AdditionalBlameMachine::blame`, return a defined fault (e.g., blame the out-of-range party or reject) when `!commitments.contains_key(&sender) || !enc_keys.contains_key(&recipient)`; replace `self.enc_keys[&decryptor]` (`encryption.rs:388`) and `self.commitments[&sender]` (`lib.rs:599`) with `get(...).ok_or(...)`-style lookups. Additionally, validate `accuser`/`faulty` against the session's `n` at the transaction decoding/handling layer (`coordinator/src/tributary/transaction.rs:346-353` and the `VerifyBlame` handler in `processor/src/key_gen.rs:504-556`).

### Proof of Concept
```rust
// Conceptual PoC against the pedpop library API
// After a normal DKG completes with params (t, n), obtain an AdditionalBlameMachine:
let mut machine = AdditionalBlameMachine::<Ristretto>::new(context, params.n(), commitment_msgs)?;

// Craft a blame statement where the accuser (recipient/decryptor) is out of range:
let fake_accuser = Participant::new(params.n() + 1).unwrap(); // valid Participant, not in set
let accused = Participant::new(1).unwrap();

// EncryptedMessage with a self-signed, valid PoP binding `from = accused`:
let msg: EncryptedMessage<Ristretto, SecretShare<_>> = encrypt_to_self(accused, /* ... */);

// Any non-None blame proof; panics at `self.enc_keys[&decryptor]` in
// crypto/dkg/pedpop/src/encryption.rs:388 during DLEq argument evaluation
let _ = machine.blame(accused, fake_accuser, msg, Some(dummy_proof));
// => thread panics: "key not found" (HashMap index on absent Participant)
```

At the protocol layer this is exercised by an `InvalidDkgShare`/`VerifyBlame` payload whose `accuser` (or `faulty`) field encodes `n + 1`, which deserializes successfully (`Participant::new` accepts any nonzero `u16`) and panics the processor during `AdditionalBlameMachine::blame`.