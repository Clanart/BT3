### Title
Encrypted DKG shares are not bound to their intended recipient, so any observer can frame an honest participant as a faulty "recipient" during blame resolution — ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
The bug class is "authorizing more than required": an ERC20 `approve` granted all tokens instead of the needed amount. In Serai's PedPoP DKG, the analog is that an `EncryptedMessage`'s proof-of-possession authorizes the message to *any* claimed recipient instead of only the intended decryptor. `pop_challenge` binds the sender (`from`), the per-message key, and the ciphertext, but the recipient's encryption public key is used only in the ECDH — it is never bound into the signature or the message.

### Finding Description
`encrypt` computes the PoP over `(context, pub_nonce, pub_key, from, msg)` while the recipient key `to` is used only inside `ecdh::<C>(&key, to)` (`encryption.rs:135-167`). The `EncryptedMessage` struct contains only `key`, `pop`, and `msg` — no recipient field (`encryption.rs:81-93`).

Blame resolution in `Decryption::decrypt_with_proof` verifies that PoP and then, if a proof is supplied, checks the DLEq against `self.enc_keys[&decryptor]`; with `proof: None` it returns `DecryptionError::InvalidProof` (`encryption.rs:366-396`). `BlameMachine::blame_internal` maps `InvalidProof` to blaming `recipient` (`lib.rs:582-588`) and `BlameMachine::blame`/`AdditionalBlameMachine::blame` take `sender` and `recipient` purely from caller input (`lib.rs:623-632`, `lib.rs:674-682`). `AdditionalBlameMachine::new` requires only the public commitment messages (`lib.rs:649-661`), so even a non-participant can evaluate blame.

Attack: an observer grabs any valid `EncryptedMessage` authored by sender `S` (share messages are broadcast over the network). They call `blame(sender=S, recipient=R, msg, proof=None)` naming an honest `R` who was never the decryptor. The PoP verifies (it only binds `from=S`), `proof` is `None` → `InvalidProof` → `blame_internal` returns `R`. `R`, who did nothing, is identified as the faulty party. In the processor this yields `ProcessorMessage::Blame { participant: accuser/R }` and a fatal slash (`processor/src/key_gen.rs:543-563`).

### Impact Explanation
An unprivileged party can cause an honest DKG participant to be adjudicated "at fault" — which Serai treats as fatal slashing — using only a publicly broadcast encrypted share and no proof at all. This is a reachable incorrect-verifier outcome: the blame procedure accepts a claim it cannot justify because the message never authorized a specific recipient, mirroring how `approve(max)` grants more authority than the message intended.

### Likelihood Explanation
Share messages are necessarily visible to all participants (and per `AdditionalBlameMachine::new`'s docs, blame can even be evaluated by non-participants). The attack requires no threshold cooperation, no key knowledge, and no invalid cryptographic object — just renaming the claimed recipient. The only prerequisites are that a blame evaluation is triggered and that the DLEq verification is skipped/fails, which `proof: None` guarantees.

### Recommendation
Bind the recipient into the message: include the recipient's encryption public key (or `Participant` index) in `pop_challenge` and in the cipher context, so `decrypt_with_proof`/`blame` can only ever resolve against the genuinely intended decryptor. Alternatively/additionally, require a valid `EncryptionKeyProof` (DLEq against `enc_keys[recipient]`) before the `recipient`-at-fault branch can be reached, rather than defaulting `InvalidProof` to blaming the claimed recipient.

### Proof of Concept
```rust
// Any observer with a broadcast EncryptedMessage from sender S
// (intended for recipient Y) frames honest R:

let msg: EncryptedMessage<C, SecretShare<C::F>> = share_from_S_to_Y.clone();

let blame_machine = AdditionalBlameMachine::new(
  context, // public DKG context
  params.n(),
  commitment_msgs, // public, authenticated commitment messages
).unwrap();

// msg.pop.verify succeeds: pop_challenge binds only `from = S`, not the
// recipient. proof = None -> DecryptionError::InvalidProof ->
// blame_internal returns `recipient`, i.e. honest R.
let faulty = blame_machine.blame(S, R, msg, None);
assert_eq!(faulty, R); // R is slashed despite never being the decryptor
```