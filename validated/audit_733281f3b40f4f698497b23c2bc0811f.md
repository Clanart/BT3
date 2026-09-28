### Title
`BlameMachine::blame`/`Decryption::decrypt_with_proof` attributes fault to a caller-chosen `recipient` identifier never bound to the message — any participant can frame an innocent party as faulty - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The external report describes an update handler that applies an operation to whichever account identifier the caller names, without verifying it against the authenticated party. The same class exists in PedPoP's blame evaluation: `blame(sender, recipient, msg, proof)` decides fault between two caller-supplied `Participant` identifiers, but the only identity cryptographically bound into the `EncryptedMessage` is `sender` (via the PoP challenge). The `recipient`/`decryptor` identifier is never authenticated against the message — `decrypt_with_proof` returns `InvalidProof` whenever no proof is supplied or the DLEq fails, and `blame_internal` then returns `recipient` as the faulty party. An attacker holding any validly-signed `EncryptedMessage` can name an arbitrary victim as `recipient` and have that victim returned as the faulty participant, earning them a fatal slash for a fault they never committed.

### Finding Description
`pop_challenge` commits the context, PoP nonce `R`, ephemeral key, the sender index (`from`), and the ciphertext — but not the intended recipient or the recipient's registered encryption key. In `encrypt`, the PoP is produced as `pop_challenge(context, pub_nonce, pub_key, from, msg)` with `from = self.i`; the recipient only enters implicitly through the ECDH key `ecdh(key, to)` used for the stream cipher. (File: crypto/dkg/pedpop/src/encryption.rs:161-165)

In `decrypt_with_proof`, `from` is verified via `msg.pop.verify(...)` against `pop_challenge(self.context, msg.pop.R, msg.key, from, ...)`, while `decryptor` is used only as a lookup key into `self.enc_keys` for the DLEq. If `proof` is `None`, the function unconditionally returns `Err(DecryptionError::InvalidProof)`; if `Some(proof)`, the DLEq must tie `enc_keys[decryptor]` to `msg.key`, which the accuser cannot satisfy for anyone but themselves. (File: crypto/dkg/pedpop/src/encryption.rs:374-396)

`blame_internal` maps `InvalidProof` directly to `return recipient` — i.e., the party named as `recipient` is declared faulty whenever a decryption proof is absent or fails, even though nothing in the message or proof establishes that this `recipient` was the intended decryptor or even participated in the exchange. `BlameMachine::blame` and `AdditionalBlameMachine::blame` both delegate to this without checking that `recipient` is the party who actually generated `proof`, or that `recipient != sender`. (File: crypto/dkg/pedpop/src/lib.rs:575-632, 674-682)

The doc comment states the message "must have been authenticated as actually having come from the sender in question" — authentication of `sender` is assumed — but there is no corresponding requirement or mechanism binding `recipient` to the accusation. (File: crypto/dkg/pedpop/src/lib.rs:615-617)

### Impact Explanation
In Serai, a blame verdict results in a fatal slash of the identified participant (see the processor commentary: "this being called means *someone* is getting fatally slashed"). An unprivileged participant can cause an honest validator to be identified as the faulty party in a DKG, slashing their bond and removing them from the validator set, using only a legitimately-received `EncryptedMessage` and public commitment messages — no key compromise or collusion required. Repeated across sessions this enables targeted removal of honest participants. Additionally, because the `recipient` identity is unbound, the mechanism meant to attribute fault can be inverted: a genuinely malicious sender's victim cannot reliably pin blame on the sender, since the same message can be re-attributed.

### Likelihood Explanation
The attack requires only: (1) participation in or observation of a PedPoP session sufficient to obtain one valid `EncryptedMessage` from any sender `S` (each participant receives one directed at them per sender), and (2) the commitment messages, which are public inputs to `AdditionalBlameMachine::new`. The attacker then calls `blame(S, victim, msg, None)`. The PoP verifies (it is a genuine signature from `S`), the `proof: None` branch immediately yields `InvalidProof`, and `victim` is returned as faulty. There is no guard rejecting a `recipient` that did not produce the proof, and no binding between `victim`'s encryption key and the message. Deterministic and costless beyond normal protocol participation.

### Recommendation
Bind the intended recipient into the encrypted message's authentication: include the recipient's `Participant` index and/or their registered encryption public key (`enc_keys[recipient]`) in `pop_challenge` inside `encrypt` and `decrypt`/`decrypt_with_proof`. Separately, harden `blame_internal`/`decrypt_with_proof` so that `InvalidProof` cannot be produced for a `recipient` who demonstrably never generated a proof — e.g., require the accusation to carry the `EncryptionKeyProof` produced by the claimed recipient (a `None` proof should only ever blame `sender`, matching the `InvalidSignature` path), and verify the DLEq against the accuser-authenticated recipient key rather than an opaque caller-chosen index.

### Proof of Concept
```rust
// Any PedPoP session: S (honest sender) -> A (attacker) sends a valid share.
// V (victim) is any other participant with a registered enc_key.
let msg: EncryptedMessage<C, SecretShare<C::F>> = /* S's real encrypted share to A */;

// Attacker evaluates (or submits to a coordinator evaluating):
let faulty = blame_machine.blame(
    S,      // sender: real signer of msg, so msg.pop.verify passes
    V,      // recipient: attacker-chosen victim — never verified
    msg,
    None,   // proof: None -> decrypt_with_proof returns InvalidProof
);
// InvalidProof -> `return recipient` -> faulty == V.
// V is reported as the faulty participant and fatally slashed,
// despite msg never being addressed to V and V doing nothing.
assert_eq!(faulty, V);
```

Root cause confirmed in code: the `proof: None` arm unconditionally returns `DecryptionError::InvalidProof` (crypto/dkg/pedpop/src/encryption.rs:394-396), `blame_internal` maps `InvalidProof` to `return recipient` (crypto/dkg/pedpop/src/lib.rs:586-587), and the recipient identity is absent from `pop_challenge` in both `encrypt` and `decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs:164, 376).