### Title
Attacker-controlled participant indexes in `BlameMachine::blame`/`AdditionalBlameMachine::blame` cause a reachable panic (denial of service) via unchecked `HashMap` indexing - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2019-12436 is an authenticated-party NULL-dereference DoS in Samba's LDAP server. The Serai analog is an unchecked-lookup panic in the PedPoP DKG blame path: `blame()` accepts attacker-supplied `sender`/`recipient` `Participant` values and indexes `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without validating membership, panicking when the index names a participant that was never registered (e.g. `> n`, or one absent from the commitment map). A single crafted blame accusation aborts the node's DKG blame evaluation and, in `AdditionalBlameMachine`, crashes even non-member observers.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` take `sender`, `recipient`, an `EncryptedMessage`, and an optional `EncryptionKeyProof`, all derivable from attacker-published bytes (`EncryptedMessage::read`, `EncryptionKeyProof::read`). Both funnel into `blame_internal`, which performs two unchecked `HashMap` index operations:

- `Decryption::decrypt_with_proof` builds the DLEq statement with `self.enc_keys[&decryptor]` (encryption.rs:388). `enc_keys` is only populated for participants whose `EncryptionKeyMessage` was registered via `register` (encryption.rs:351-361). If `recipient` (passed as `decryptor`) is any `Participant` not in `1..=n` — or whose message was absent — this indexing panics, and it is evaluated as an argument *before* the DLEq proof is even checked, so the attacker only needs a valid Schnorr PoP on their message (which anyone can produce over `msg.key`) plus any `EncryptionKeyProof`.

- If `proof` is `None` or that path is not taken, `blame_internal` later evaluates `self.commitments[&sender]` (lib.rs:599). `commitments` is keyed by registered participants only; a `sender` value outside that set panics. To reach it, the attacker supplies a `msg` with a valid PoP signature (verified at encryption.rs:374-379 using the attacker-chosen `from`/`sender` in the challenge) and a payload that deserializes to a canonical scalar — both fully attacker-controlled.

Notably, `AdditionalBlameMachine::new` exists precisely so *non-participants* can evaluate blame over arbitrary published commitment messages, and `blame()` on it accepts arbitrary `sender`/`recipient` arguments — so a completely unprivileged observer-triggered panic is reachable. The crate's own doc warns only that *invalid commitments* are "undefined behavior, and may cause ... panics" (lib.rs:648); invalid `sender`/`recipient` indexes are not documented as caller obligations.

### Impact Explanation
Analogous to CVE-2019-12436 (authenticated remote crash of a service), any party able to submit a blame accusation — or feed `sender`/`recipient`/`msg`/`proof` into a host that calls `blame` — can panic the host process. In a validator/node deployment this is an unprivileged availability failure of the DKG/blame-evaluation path: the panic aborts the calling thread rather than returning a `PedPoPError`, and since `blame` consumes/uses `self` inside library internals, the process cannot gracefully continue unless the host wraps every call in `catch_unwind`. Impact is DoS only — no key material is revealed — consistent with the source advisory's Medium (CVSS 6.5, availability-only) rating.

### Likelihood Explanation
The blame API's entire purpose is to adjudicate *accusations between mutually distrustful parties*, so `sender` and `recipient` are inherently adversary-influenced values. A malicious participant names `recipient = Participant(n + 1)` (a `Participant` is any nonzero `u16`; nothing restricts it to `1..=n`) with a self-signed PoP and a dummy `EncryptionKeyProof`, and any node calling `blame()` panics at encryption.rs:388 before verifying anything meaningful. No cryptographic validity of the accusation is required — only a well-formed encoding and a Schnorr signature the attacker generates themselves. Likelihood is therefore high wherever blame evaluation is exposed to unvetted inputs.

### Recommendation
Validate `sender`/`recipient` membership before indexing in `blame_internal` and `decrypt_with_proof`: return the accusing/`recipient` party (or a new `PedPoPError`/`DecryptionError` variant) when `!self.commitments.contains_key(&sender)` or `!self.enc_keys.contains_key(&decryptor)`, instead of relying on `HashMap`'s panicking `Index` impl. Consider bounding `Participant` values to `1..=n` at the `blame()` API boundary for defense in depth.

### Proof of Concept
Conceptual (attacker is a non-member observer; member case is analogous):

1. Honest DKG runs with `n = 3`, producing `AdditionalBlameMachine::new(context, 3, commitment_msgs)` — `commitments`/`enc_keys` now hold exactly participants `{1,2,3}`.
2. Attacker constructs `EncryptedMessage::<C, SecretShare<C::F>>` bytes: `key = k*G` for any scalar `k`, `pop = SchnorrSignature::sign(k, r, pop_challenge(context, r*G, k*G, Participant(4), share_bytes))` with `from = Participant::new(4)`, and `msg` = valid `C::F::Repr` of any scalar. (Signing with `sender = 4` is fine — the PoP is self-referential and verifies.)
3. Attacker submits `blame(sender = Participant(4), recipient = Participant(9), msg, proof = Some(any EncryptionKeyProof))` to the node.
4. `decrypt_with_proof` verifies the PoP (passes — it binds to `from = 4`, which the attacker controls), then evaluates `self.enc_keys[&Participant(9)]` at encryption.rs:388 → **panic: "key not found"** (HashMap index on absent key). If `proof` were `None`, the code proceeds to `self.commitments[&Participant(4)]` at lib.rs:599 after the scalar check → same panic.

Result: one unauthenticated-in-effect message crashes the blame-evaluation path of every node (including non-member `AdditionalBlameMachine` observers) processing it — a NULL-deref-class availability bug reachable purely from public inputs.