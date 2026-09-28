### Title
Forged blame: any participant can fabricate an `EncryptedMessage` that causes `blame` to fault an honest, uninvolved sender - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` (used by `BlameMachine::blame` / `AdditionalBlameMachine::blame` to decide fault in the PedPoP DKG) "authenticates" the accused sender only through the `from: Participant` value mixed into `pop_challenge`. The Schnorr proof-of-possession is verified against `msg.key`, an ephemeral point wholly chosen by whoever constructed the message, so anyone can produce a valid PoP claiming an arbitrary `from`. Combined with an `EncryptionKeyProof` the accuser can generate for their own registered encryption key, an unprivileged party can present a fabricated share message that decrypts to an invalid share and get the honest `accused` party blamed.

### Finding Description
In `blame_internal` (crypto/dkg/pedpop/src/lib.rs:575-608), blame is assigned as `sender` whenever the decrypted bytes are not a canonical scalar or fail `share_verification_statements` against `self.commitments[&sender]`. The only thing standing between an accuser and this outcome is `decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs:366-397), which performs two checks:

1. `msg.pop.verify(msg.key, pop_challenge(context, msg.pop.R, msg.key, from, msg.msg))` — but `msg.key` is attacker-chosen. The attacker generates `key` themselves, so they know its discrete log and can sign `pop_challenge` for any `from` value. `from` is only a transcript label; it is not bound to any key the accused controls. This is exactly the bug-class shape of GHSA-8xv7-89vj-q48c: an accessor that appears policy-restricted (`pop` binding `sender`) while the reachable path (`format_map`-style) bypasses the intended restriction — the PoP proves possession of the ephemeral key, not authorship by `from`.
2. `proof.dleq.verify(..., &[G, msg.key], &[enc_keys[decryptor], proof.key])` — binds the revealed ECDH point to the *accuser's* registered encryption key. The accuser knows their own `enc_key` private scalar, so they compute `proof.key = enc_priv * msg.key` and produce a valid DLEq for the self-chosen `msg.key`.

After both checks, `cipher(context, proof.key)` is applied to attacker-chosen ciphertext bytes, yielding plaintext the attacker fully controls (they picked ciphertext; keystream is computable since they know `proof.key`'s scalar relationship). They set plaintext to a non-canonical scalar or a valid scalar that fails `share_verification_statements` against the accused's commitments, and `blame_internal` returns `sender` — i.e., the accused — as faulty.

This is reachable by an unprivileged party: in `processor/src/key_gen.rs` (`CoordinatorMessage::VerifyBlame`, lines 504-563), the `accuser`, `accused`, `share` bytes, and `blame` bytes all come from the accusation message. `EncryptedMessage::read` (encryption.rs:170-177) applies no identity or sender-binding check; `AdditionalBlameMachine::new` (lib.rs:649-662) only registers the recorded commitment messages. Nothing ties the accused to the fabricated ciphertext. The same applies in-protocol via `KeyMachine::calculate_share`: the `shares` `HashMap` is keyed by claimed sender, and a malicious peer can place a crafted `EncryptedMessage` under an honest participant's index — since they control `msg.key`, the PoP verifies under `from = victim`, the ECDH against the victim's registered enc_key is computable by anyone (it's `victim_enc_pub * attacker_scalar`), decryption yields attacker-chosen bytes, and `InvalidShare { participant: victim }` is emitted, with a blame proof that further "proves" the victim's fault to third parties.

### Impact Explanation
An unprivileged participant (or anyone able to submit shares/blame to the protocol) can cause an honest party to be determined faulty. In the Serai processor this surfaces as `ProcessorMessage::Blame { participant: accused }`, which per the code's commentary results in a fatal slash of the blamed validator. It also produces self-consistent, publicly-verifiable blame evidence (`EncryptionKeyProof` + DLEq) incriminating a party who never sent the message, corrupting the audit trail the blame system exists to provide.

### Likelihood Explanation
The attack requires only public inputs the attacker already possesses: the victim's registered encryption public key and commitment message (both broadcast), plus knowledge of their own encryption private key. All quantities are computable in milliseconds — pick `k`, set `msg.key = k*G`, sign the PoP for `from = victim`, compute `proof.key = k * victim_enc_pub` with the matching DLEq (proven with scalar `k`, which the attacker knows), and choose ciphertext bytes that decode to a bad share. No threshold collusion, no Malicious-active-in-DKG advantage, no leaked keys needed. Every DKG session with ≥2 participants is exposed whenever an accusation can be raised or a share can be injected under a victim's index.

### Recommendation
Bind the blame-decision path to something the accused uniquely controls. Options:

- Require the share `EncryptedMessage` to be accompanied by a signature from the accused's registered encryption key (or validator key) over the serialized message, verified in `decrypt_with_proof`/`blame_internal` before any decryption occurs.
- Alternatively, fold the accused's *registered* identity into the PoP cryptographically — e.g., require `msg.key` to be derived as `k * enc_keys[from]` for a proven `k` (DLEq against `enc_keys[from]` rather than just against `enc_keys[decryptor]`), so only the holder of a key bound to `from` could have constructed the message, or extend `pop_challenge` to be verified under a per-participant key rather than the ephemeral `msg.key`.
- At minimum, `blame_internal` must not return `sender` for a message whose authenticity cannot be established; unverifiable authorship should abort the protocol without assigning blame.

### Proof of Concept
Attacker A (participant index a, enc private scalar `e_a`, registered `enc_pub_a = e_a*G`) wants to frame honest participant V (index v, registered `enc_pub_v`):

```rust
// Inside a blame evaluation with context `ctx`:
// Fabricate an EncryptedMessage "from" v.
let k = <C as Ciphersuite>::F::random(&mut OsRng);          // scalar we know
let msg_key = C::generator() * k;                            // attacker-chosen ephemeral key
let ecdh_point = enc_pub_v * k;                              // = e_v * msg_key; known to us via k

// Ciphertext: any bytes; decrypts under cipher(ctx, ecdh_point) to our choice.
// Choose plaintext p that is NOT a valid share for v (e.g. all-0xFF, non-canonical).
let mut msg_bytes = p;
cipher::<C>(ctx, &Zeroizing::new(ecdh_point)).apply_keystream(&mut msg_bytes);

// Valid PoP since we know dlog(msg_key) = k; `from` is just a label.
let r = C::random_nonzero_F(&mut OsRng);
let R = C::generator() * r;
let pop = SchnorrSignature::sign(
    &Zeroizing::new(k), Zeroizing::new(r),
    pop_challenge::<C>(ctx, R, msg_key, /* from = */ V, &msg_bytes),
);

// Valid EncryptionKeyProof: DLEq over [G, msg_key] -> [enc_pub_v, ecdh_point] with scalar k.
let proof = EncryptionKeyProof {
    key: Zeroizing::new(ecdh_point),
    dleq: DLEqProof::prove(&mut OsRng, &mut encryption_key_transcript(ctx),
                           &[C::generator(), msg_key], &Zeroizing::new(k)),
};

let forged = EncryptedMessage { key: msg_key, pop, msg: SecretShare(msg_bytes) };

// Accusation: (sender = V, recipient = A, msg = forged, proof = Some(proof))
// blame_internal: PoP verifies (we hold k); DLEq verifies (enc_keys[A] path — note the
// proof binds to the *accuser's* key, which we control); decryption yields p;
// share_verification_statements(V's commitments, p) fails => returns V as faulty.
assert_eq!(machine.blame(V, A, forged, Some(proof)).1, V);
```

Two paths reach the vulnerable code:

- `KeyMachine::calculate_share` (lib.rs:463-499): attacker inserts the forged message into `shares` under key `V`; `decrypt` uses `enc_keys`/`msg.key` ECDH which the attacker computed, and `InvalidShare { participant: V }` results even though V sent nothing.
- `VerifyBlame` handling (processor/src/key_gen.rs:504-563) → `AdditionalBlameMachine::blame` → `blame_internal`, where the accuser supplies `share` and `blame` bytes directly.

The root cause is that `pop_challenge`'s `sender` field (encryption.rs:302-324) is a label, not a cryptographic identity: the signature is verified against `msg.key`, which the message author selected, so the check "the sender knows this key's discrete log" never establishes "the sender is `from`".