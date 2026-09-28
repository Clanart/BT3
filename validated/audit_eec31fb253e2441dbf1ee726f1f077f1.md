### Title
Unauthenticated, unbounded blame adjudication lets any non-participant obtain faulty-party verdicts against honest DKG members - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
The analog of `setPoolActive` lacking access control is `AdditionalBlameMachine::new`/`blame` in PedPoP: a state-changing adjudication API explicitly exposed to parties who were never members of the DKG, which accepts attacker-chosen `sender`/`recipient` `Participant` indexes and attacker-supplied `EncryptedMessage`/`EncryptionKeyProof` bytes, then returns which participant is "faulty". Because `blame_internal` never checks that the accused `recipient` actually raised an accusation, nor that the supplied share message is bound to the claimed `recipient` index, an unprivileged observer can manufacture blame verdicts against honest participants, which downstream callers (e.g., the processor's `InvalidParticipant`/removal flow) treat as grounds for removal/slashing.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` funnel into `blame_internal`, which is documented as usable by non-members:

- `AdditionalBlameMachine::new` states it creates "an AdditionalBlameMachine capable of evaluating Blame regardless of if the caller was a member in the DKG protocol" (crypto/dkg/pedpop/src/lib.rs:639-661).
- `blame_internal` takes `sender`, `recipient`, the share `msg`, and a `proof`, then:
  1. calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)`; `InvalidSignature` ⇒ `sender` faulty, `InvalidProof` ⇒ `recipient` faulty (lines 582-588),
  2. verifies the decrypted share against `share_verification_statements::<C>(recipient, &self.commitments[&sender], ...)` — i.e., the sender's polynomial evaluated at the *attacker-supplied* `recipient` index (lines 595-605),
  3. returns `recipient` as faulty whenever the share *does* verify (lines 607-608).

Two missing checks make this reachable by an unprivileged party:

1. **No binding between the accusation and the accused.** The function is invoked "given an accusation of fault", yet nothing ties the input to a real complaint by `recipient`. Anyone holding the broadcast `EncryptedMessage` from `sender` to `j` (a public/relayed object; `EncryptedMessage::read` is a listed untrusted-bytes surface) plus the corresponding `EncryptionKeyProof` can call `blame(sender, j, msg, proof)`. A *valid* share causes `blame_internal` to return `j` — marking the honest recipient faulty even though they never claimed anything. Any caller that slashes/removes the returned party has just punished an honest member on attacker initiative alone.

2. **Unvalidated participant indexes.** `recipient` is used directly as the polynomial evaluation point and `sender` is used as a `HashMap` key (`self.commitments[&sender]`). A `sender` outside `1..=n` panics outright (aborting the state machine — the same class of "gate the protocol" impact as `setPoolActive` toggling `onlyActivePool`), and a `recipient` index that was not the message's true recipient makes the share-verification multiexp evaluate the sender's commitments at a bogus point, yielding an incorrect `sender`/`recipient` fault verdict.

### Impact Explanation
Blame verdicts are the mechanism by which faulty DKG participants are identified; returning an honest participant as `faulty` leads to their removal/slashing and can abort the key-generation session — functionally equivalent to an unauthorized party flipping `setPoolActive` and freezing `allocate`/`distribute`. It requires no key material and no collusion: only relayed protocol messages (`EncryptionKeyMessage<Commitments>`, `EncryptedMessage<SecretShare>`, `EncryptionKeyProof`) that the API was designed to accept from non-members.

### Likelihood Explanation
The constructor explicitly advertises non-member usage, so any integration that runs blame evaluation on messages received over an unauthenticated/broadcast channel is exposed. The only prerequisites are the already-public commitment messages (to build `commitments`/`Decryption` via `AdditionalBlameMachine::new`) and one leaked or relayed share message with its ECDH proof — precisely the objects blame proofs are designed to publish.

### Recommendation
- Require the blame claim itself to be authenticated by the accusing `recipient` (e.g., a signature over `(sender, recipient, msg_hash)` bound to the session `context`), so `blame` can only return `recipient` when that party actually complained.
- Validate `sender` and `recipient` against `1..=n` (and against `commitments.keys()`) before indexing or evaluating, returning a typed error instead of panicking or mis-attributing.
- Bind the decrypted share check to the `recipient` encoded in the message/ECDH context rather than a caller-supplied index.

### Proof of Concept
1. Observe a completed PedPoP round: collect all `EncryptionKeyMessage<C, Commitments<C>>` (broadcast), plus a relayed `EncryptedMessage<C, SecretShare<C::F>>` from honest sender `s` to honest recipient `j` and its `EncryptionKeyProof`.
2. Build `AdditionalBlameMachine::new(context, n, commitment_msgs)` — succeeds despite the attacker not being a participant.
3. Call `blame(s, j, msg, Some(proof))` on the *valid* message. `decrypt_with_proof` succeeds (the proof legitimizes the ECDH key for third-party verification), the share verifies against `s`'s commitments at `j`, and `blame_internal` returns `j` — an honest participant reported as faulty without ever having complained.
4. Variant: call `blame(s, j, msg, proof)` but pass a different `recipient` index `r` (or out-of-range `sender`) — the verdict flips to `r`/`s` or panics at `self.commitments[&sender]`, demonstrating the indexes are entirely attacker-controlled with no membership or binding check.