### Title
Swapped `sender`/`recipient` arguments to `BlameMachine::blame` cause DKG blame to be attributed to the wrong party — an honest accuser can be fatally slashed (File: `processor/src/key_gen.rs`, API contract in `crypto/dkg/pedpop/src/lib.rs`)

### Summary
The ERC-721 finding is about a callback (`onERC721Received`) being invoked with `address(this)` instead of the true operator, so the receiver's accept/bookkeeping decision is made on a false initiator identity. The Serai analog is the PedPoP blame adjudication path: `BlameMachine::blame(sender, recipient, msg, proof)` takes, per its own documentation, "a copy of the encrypted secret share from the accused **sender** to the accusing **recipient**", and internally decrypts with `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` and verifies the share against `self.commitments[&sender]` evaluated at `recipient`. The caller in `processor/src/key_gen.rs` invokes `.blame(accuser, accused, share, blame)` — i.e., it passes the accuser (the share's *recipient*) as `sender` and the accused (the share's actual *sender*) as `recipient`. Every identity-dependent decision inside `blame_internal` — which ECDH context to use, whose commitments to verify against, which Participant index to interpolate at, and which party to return as faulty — is therefore evaluated on swapped roles, directly mirroring the wrong-`operator` bug class.

### Finding Description
`blame` / `blame_internal` in `crypto/dkg/pedpop/src/lib.rs:623-631` and `575-609` define `sender` as the party that produced the `EncryptedMessage` and `recipient` as the accusing party:

- `decrypt_with_proof(sender, recipient, msg, proof)` returns `DecryptionError::InvalidSignature` → `sender` faulty, `InvalidProof` → `recipient` faulty (lines 582-588).
- `share_verification_statements::<C>(recipient, &self.commitments[&sender], share)` verifies the decrypted share against the **sender's** commitments at the **recipient's** index (lines 595-604); non-identity → `sender` faulty, else `recipient` faulty.

The coordinator-side adjudication in `processor/src/key_gen.rs:543-556` handles `CoordinatorMessage::VerifyBlame { accuser, accused, share, blame }`. The `share` field is the encrypted secret share sent by `accused` to `accuser` (it is fetched from `DkgShare::get(txn, genesis, accuser, faulty)` in `coordinator/src/tributary/handle.rs:486`). The correct call is therefore `blame(accused, accuser, ...)`, but the code executes `.blame(accuser, accused, substrate_share, substrate_blame)` and `.blame(accuser, accused, network_share, network_blame)` — a swapped `msg.sender`-equivalent.

Consequences of the swap inside `blame_internal`:

- ECDH/`decrypt_with_proof` resolves encryption keys under the pair `(accuser → accused)`, the inverse of the actual `(accused → accuser)` message, so a genuine accuser-supplied `EncryptionKeyProof` no longer matches the registered direction.
- Share verification uses `self.commitments[&accuser]` (the accuser's commitments) instead of the accused's, and interpolates at the `accused` index instead of the accuser's, so the validity check itself is meaningless.
- The return values `sender`/`recipient` map to `accuser`/`accused` respectively, so each blame verdict lands on the wrong role.

The wrapper then maps the result to a slash (`processor/src/key_gen.rs:558-563`): if either machine returns `accused` it emits `Blame { participant: accused }`, otherwise `Blame { participant: accuser }` — and `Blame` leads to `fatal_slash` of the named participant on the tributary.

### Impact Explanation
A validator who correctly reports an invalid DKG share (`Transaction::InvalidDkgShare`, routed to `VerifyBlame`) can be adjudicated as the faulty party and fatally slashed, while the validator who actually broadcast the malformed share escapes. Conversely a false accusation can be resolved against the accused. Since `AdditionalBlameMachine` is explicitly usable by non-participants and the coordinator path is driven by on-chain `InvalidDkgShare` transactions, any unprivileged participant who submits an accusation reaches this code. Because the verdict feeds `fatal_slash`, this is a High-impact misattribution of fault, not merely incorrect bookkeeping.

### Likelihood Explanation
Every `VerifyBlame` resolution passes through `key_gen.rs:549` and `:556` with the arguments in the wrong order, so every blame adjudication is evaluated under swapped roles. The outcome depends on which branch of `blame_internal` is hit, but no input can produce the *correctly-attributed* verification the protocol intends — the verdict is computed over the wrong sender/recipient pair in all cases.

### Recommendation
Change the calls to `.blame(accused, accuser, substrate_share, substrate_blame)` and `.blame(accused, accuser, network_share, network_blame)`, matching the documented contract that `sender` is the accused share-sender and `recipient` is the accuser. Alternatively, rename the `blame` parameters to `accused`/`accuser` (or add a `debug_assert_eq!`/type-level distinction) to prevent role confusion, and add a regression test exercising `CoordinatorMessage::VerifyBlame` for both a genuinely-invalid share and a false accusation.

### Proof of Concept
1. Participants `i` (accuser) and `j` (accused) run PedPoP; `j` sends `i` a share that fails `share_verification_statements`.
2. `i` reports via `Transaction::InvalidDkgShare`; the coordinator stores `DkgShare[(i, j)]` and sends `VerifyBlame { accuser: i, accused: j, share, blame }` (`coordinator/src/tributary/handle.rs:493-505`).
3. The processor calls `blame(i, j, msg, proof)`, so `decrypt_with_proof` uses `(sender=i, recipient=j)` — the wrong ECDH direction — and `share_verification_statements(j, &commitments[&i], share)` uses `i`'s commitments at `j`'s index.
4. Depending on where it fails, `blame_internal` returns `i` (`sender`) or `j` (`recipient`) for reasons unrelated to `j`'s actual fault; `key_gen.rs:559-563` then emits `Blame { participant: ... }` on the wrong party, and the tributary fatally slashes them.

Relevant code: `crypto/dkg/pedpop/src/lib.rs:575-632` (parameter contract), `processor/src/key_gen.rs:504-563` (swapped call site), `coordinator/src/tributary/handle.rs:486-505` (share provenance proving `accused` is the true sender).