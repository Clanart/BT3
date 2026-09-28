### Title
PedPoP encrypted secret shares are not bound to their intended recipient, enabling replay of a valid `EncryptedMessage` to falsely blame an honest sender - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Sentry advisory is a replay bug: a valid single-use artifact (an invite link) could be redeemed in a context it was never bound to (multiple accounts), because the consumption check didn't bind the token to the redeemer. The analog in Serai is in the PedPoP DKG encryption layer: `EncryptedMessage`'s proof-of-possession binds the context, the ephemeral key, the sender, and the ciphertext, but never the *recipient*. A message honestly produced by sender `s` for recipient `r` can be replayed as if it were addressed to a different participant `r'`, and the blame protocol will then convict the honest sender.

### Finding Description
`pop_challenge` (crypto/dkg/pedpop/src/encryption.rs:302-324) transcripts `context`, `nonce`, `key`, `sender`, and `msg` — there is no recipient field. The same omission exists in `cipher` (lines 101-133) and in `encryption_key_transcript` (lines 326-330), neither of which binds the decryptor's identity.

Concretely, `EncryptedMessage` contains:
- `key` = `k * G` (a fresh per-message public key),
- `pop` = Schnorr signature over `pop_challenge(context, R, k*G, sender, ciphertext)`,
- `msg` = share XOR ChaCha20 stream derived from `cipher(context, k * enc_pub_key[r])`.

Honest sender `s` encrypts share `p_s(r)` for `r` via `Encryption::encrypt` (line 460-467), using `decryption.enc_keys[&r]`. An observer (or a relay manipulating message routing — the shares map handed to `KeyMachine::calculate_share` is attacker-influenced at the protocol layer, since the library explicitly does not authenticate delivery per crypto/dkg/pedpop/src/lib.rs:616-617) can take that valid message and hand it to participant `r'` as their share from `s`. The PoP still verifies because the recipient is not part of the challenge.

`r'` decrypts with `ecdh(enc_key_{r'}, k*G)`, producing garbage bytes. In `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:476-499), `C::F::from_repr` fails on the garbage, yielding `PedPoPError::InvalidShare { participant: s, ... }` — blaming `s`. If `r'` raises a public blame accusation instead, `BlameMachine::blame_internal` (lib.rs:575-609) runs `decrypt_with_proof` (encryption.rs:366-397): the PoP verifies (valid, sender-bound), `r'` can honestly produce a valid `EncryptionKeyProof` DLEq since they know `enc_key_{r'}` (`proof.key = enc_key_{r'} * msg.key` satisfies the DLEq against `enc_keys[decryptor]`), decryption yields garbage, `from_repr` fails, and `blame_internal` returns `sender` — formally adjudicating the honest `s` as faulty.

### Impact Explanation
False-blame / protocol-abort against an honest DKG participant. Any party able to relay or withhold messages in the (unauthenticated-by-design) DKG channel can replay one participant's legitimately-encrypted share ciphertext to a different recipient slot, causing the blame machinery to convict the honest sender. On deployed flows this aborts key generation and can get an honest validator marked faulty — the same class of harm as the invite-reuse advisory (a valid credential consumed in a context it wasn't bound to, producing an unauthorized/unintended state transition). The cryptographic material itself is not leaked (the replayed ciphertext is undecryptable garbage to `r'`), so this is an integrity/availability issue, not key recovery — consistent with a Medium severity.

### Likelihood Explanation
Requires an attacker positioned to deliver DKG messages to a participant other than the intended one (routing manipulation or a cooperating session-level adversary), which the library explicitly delegates to the caller's authenticated-channel assumption. The attack is deterministic — no probing or negligible-probability events — and needs only one captured valid ciphertext. It is bounded by whether the integrator's channel already binds sender→recipient pairs; where it does (e.g., per-pair authenticated transport), the replay is infeasible, which keeps this at Medium rather than higher.

### Recommendation
Bind the recipient into both the ciphertext integrity and the cipher derivation:
- Add the recipient to `pop_challenge` (e.g., `transcript.append_message(b"recipient", to.to_bytes())`) so a message for `r` cannot validate as a message for `r'`.
- Include the recipient (or `enc_keys[recipient]`'s encoding) in `cipher`'s transcript and/or `encryption_key_transcript`, so decryption under the wrong registered key provably fails at the PoP/DLEq stage rather than producing garbage that blames the sender.

### Proof of Concept
1. Participants `s`, `r`, `r'` run PedPoP. `s` produces `m = EncryptedMessage { key: kG, pop: σ, msg: ct }` intended for `r` via `Encryption::encrypt(rng, r, share_s(r))`.
2. The adversary forwards `m` to `r'` in the `shares` map keyed under `s` in `KeyMachine::calculate_share` (lib.rs:463).
3. `r'` calls `encryption.decrypt(...)`: `σ` verifies under `pop_challenge(context, σ.R, kG, s, ct)` — recipient is absent from the challenge, so verification passes; decryption uses `ecdh(enc_key_{r'}, kG)` → garbage; `C::F::from_repr` fails → `InvalidShare { participant: s }`.
4. For public adjudication, `r'` calls `blame(s, r', m, proof)` with `proof.key = enc_key_{r'} * kG` and a DLEq they can honestly prove (they know `enc_key_{r'}`); `decrypt_with_proof` accepts the DLEq because `encryption_key_transcript` binds neither sender nor recipient, decrypts to non-canonical bytes, and `blame_internal` returns `s` — honest `s` is convicted.

Uncertain aspects: whether integrator transport (e.g., the tributary/coordinator message layer, outside the in-scope crates) already authenticates sender→recipient pairing; if it does, exploitability is reduced, but the cryptographic omission in `pop_challenge`/`cipher` remains.