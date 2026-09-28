### Title
PedPoP accepts identity encryption keys, broadcasting secret shares under a publicly-known cipher key - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
A participant in the PedPoP distributed key generation can register an encryption key equal to the group identity. No code path validates `enc_key`. Every peer then encrypts that participant's secret shares with an ECDH "shared key" that is itself the identity point — a publicly-known constant — so anyone who observes the wire ciphertext can decrypt the shares. This is a privilege-bypass analog to CVE-2023-35168: confidential per-participant data (secret shares) becomes readable by parties who were never authorized to see it.

### Finding Description
`Decryption::register` stores `msg.enc_key` verbatim with no identity/torsion check (`encryption.rs:351-362`). `Encryption::encrypt` passes that key into `ecdh(private, public) = public * private` (`encryption.rs:95-97`, `466`). When `enc_key = G * 0` (identity), `ecdh` returns the identity point, and `cipher` derives the ChaCha20 key from `transcript(context || identity.to_bytes())` (`encryption.rs:101-133`) — all public inputs. The ciphertext is therefore decryptable by any observer, including other participants or a passive eavesdropper on the authenticated channel.

The `EncryptionKeyMessage` carrying `enc_key` is read by `EncryptionKeyMessage::read` (`encryption.rs:57-59`), which only checks canonical encoding via `C::read_G` — identity is a canonical encoding. `SecretShareMachine::verify_r1` validates the commitment PoK (`pedpop/src/lib.rs:323-329`) but never inspects `enc_key` before it is registered at line 315 (`self.encryption.register(l, msg)`). Subsequently `generate_secret_shares` encrypts each victim's share to the malicious participant (`pedpop/src/lib.rs:366-369`) under the null ECDH.

### Impact Explanation
Every honest participant encrypts their secret-share evaluation `f_i(l)` for the malicious participant `l` under a keystream any third party can reproduce. An observer recovers all `n-1` plaintext shares destined for `l`. While a single participant's received shares are `t` independent evaluations and do not alone recover the group secret, the DKG's confidentiality guarantee — that shares are readable only by their intended recipient — is silently voided, and combined with any single compromised/blame-revealed ECDH key elsewhere this degrades to partial share exposure that PedPoP's design explicitly tries to prevent (see the commentary at `encryption.rs:84-90` on why leaking even one message is a "massive side effect").

### Likelihood Explanation
The attack requires only that one DKG participant broadcast a malformed `EncryptionKeyMessage` — unauthenticated bytes fully within the attacker's control (`read` → `validate_map` → `register`). It needs no collusion, no threshold of malicious parties, and is undetectable: the identity key is well-formed encoding, decryption succeeds for the attacker, and share verification passes because shares themselves are honestly generated.

### Recommendation
Reject non-contributory/identity group elements for `enc_key` in `Decryption::register` (and likewise for `msg.key` in `EncryptedMessage::read`/`decrypt`), e.g. `if bool::from(enc_key.is_identity()) { Err(...) }`, matching the `random_nonzero_F` discipline already used when generating keys. Optionally require a proof of possession of the `enc_key` discrete log, binding it to `participant` and `context`.

### Proof of Concept
1. Malicious participant `l` builds `EncryptionKeyMessage { msg: Commitments(valid), enc_key: C::G::identity() }` and broadcasts it.
2. `verify_r1` passes: commitments PoK verifies; `register(l, msg)` stores identity with no error (`encryption.rs:360`).
3. Each honest peer `i` calls `encrypt(rng, context, i, identity, share_i(l))` → `ecdh = identity * k_i = identity` → keystream `K = ChaCha20(transcript(context, identity_bytes))`.
4. Observer computes the same `K` from public `context` and `identity.to_bytes()` and XORs the captured `EncryptedMessage.msg`, recovering all `share_i(l)` plaintexts.

```rust
// crypto/dkg/pedpop/src/encryption.rs:95-97
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref()) // public = identity ⇒ shared key = identity
}
```

```rust
// crypto/dkg/pedpop/src/encryption.rs:356-361
self.enc_keys.insert(participant, msg.enc_key); // no identity/torsion check
```