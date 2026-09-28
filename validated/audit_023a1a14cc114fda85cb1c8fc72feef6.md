### Title
Unauthenticated blame attribution lets any third party flag an honest recipient as faulty using a publicly broadcast share message - (crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept caller-supplied `sender`, `recipient`, `msg`, and `proof` arguments and emit a `Participant` identified as faulty. The API documents that `msg` must be authenticated as coming from `sender`, but nothing binds the *accusation itself* to `recipient`. When the supplied message is a valid, authentic encrypted share — which is publicly observable in PedPoP deployments where messages are broadcast (e.g., via tributary) — `blame_internal` returns `recipient` as the faulty party, declaring an honest participant a liar for an accusation they never made.

### Finding Description
In `blame_internal` (pedpop/src/lib.rs:575-609), the logic is:

```rust
let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
  Ok(share_bytes) => share_bytes,
  Err(DecryptionError::InvalidSignature) => return sender,
  Err(DecryptionError::InvalidProof) => return recipient,
};
...
// The share was canonical and valid
recipient
```

The `recipient` return value at the end encodes the assumption "the recipient claimed this share was invalid, yet it verifies, so the recipient is at fault." That inference is only sound if the caller is acting on a genuine accusation made by `recipient`. `AdditionalBlameMachine::new` (lines 649-662) is explicitly designed so "the caller [need not be] a member in the DKG protocol," and its `blame` method (lines 674-682) accepts arbitrary `sender`/`recipient`/`msg`/`proof` with no check that `recipient` ever accused `sender`.

Because PedPoP share messages are transported over a public medium (the coordinator publishes `DkgShares` on the tributary, and `DkgShare::get` stores them), any observer possesses a copy of a fully authentic `EncryptedMessage` from honest sender `i` to honest recipient `j`. Feeding `(sender=i, recipient=j, msg=authentic_msg, proof=valid_revealed_key)` into `blame` satisfies every documented precondition — the message genuinely came from `sender` — yet deterministically returns `j` as faulty. The same `recipient`-blaming output is reachable via the `InvalidProof` branch by omitting/supplying a mismatched `proof` for a message that does authenticate.

### Impact Explanation
`blame` is the arbiter of fault in the DKG; its output is intended to drive slashing/removal of the identified participant. An unprivileged party with only public data can produce a blame verdict naming an honest `recipient` as faulty, even though that party never submitted any accusation and the underlying share was correct. Downstream consumers that treat the returned `Participant` as a slashable fault can penalize honest validators — a wrongful-conviction primitive, the analog of "anyone can set merkle roots for any vault" (anyone can attribute fault to any participant). Severity is bounded by the fact that callers must transport blame through an authenticating channel (e.g., the coordinator's `InvalidDkgShare` path, which does additionally require `accuser` to belong to the signer's index range), but the library API itself performs no such binding and its documented preconditions are all satisfiable by a third party.

### Likelihood Explanation
Triggering requires only a publicly transmitted `EncryptedMessage` and a call to `blame`/`AdditionalBlameMachine::blame` with attacker-chosen `sender`/`recipient`. No key material, threshold cooperation, or malformed encodings are needed — all required bytes are legitimately published protocol data.

### Recommendation
Bind the accusation to the accuser: require `msg` to be accompanied by proof that `recipient` actually accused `sender` (e.g., a signature over `(sender, msg_hash)` from the recipient's encryption/PoP key), or change the API so the `recipient` role is implicit (derived from the caller's key material, as `Decryption::new` does for the in-protocol `BlameMachine`) rather than a free parameter. At minimum, `AdditionalBlameMachine::blame` should not return `recipient` for a valid share unless the recipient's accusation is independently authenticated.

### Proof of Concept
```rust
// Any observer of the public channel holds msg: the authentic
// EncryptedMessage<C, SecretShare<C::F>> from honest sender i to honest
// recipient j, plus the revealed ECDH proof if published.
let machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();

// Preconditions hold: msg is genuinely from `sender` to `recipient`.
let faulty = machine.blame(sender_i, recipient_j, msg, Some(valid_proof));

// j never accused anyone, yet is identified as the faulty party.
assert_eq!(faulty, recipient_j); // honest participant blamed
```
This works because `blame_internal` returns `recipient` whenever the decrypted share is canonical and passes `share_verification_statements` — which is the expected outcome for every honestly generated share.