### Title
Missing identity/PoK check on PedPoP registered encryption keys lets a participant publish a share to the world - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

CVE-2021-26758 is a privilege-escalation bug: a low-privilege principal uses an unchecked input path to obtain capabilities reserved for root. The Serai analog is a *share-confidentiality privilege escalation* in the PedPoP DKG: `Decryption::register` stores each participant's static encryption public key (`msg.enc_key`) with **no proof of possession and no identity-point rejection**, even though `frost`'s own `Curve::read_G` explicitly rejects identity and PedPoP's `EncryptedMessage` requires a per-message PoP for exactly this class of attack.

A malicious participant registers `enc_key = identity` (or any key whose discrete log is publicly known/derivable, e.g. a key copied from a public context). Every honest dealer then encrypts that participant's secret share as `cipher(context, ecdh(msg_key, enc_key))`, where `ecdh` reduces to the identity point — a value known to everyone. Any passive observer reconstructs the ChaCha20 keystream and recovers the plaintext `SecretShare` destined for that participant.

### Finding Description

`Encryption::register`/`Decryption::register` inserts `msg.enc_key` verbatim into `enc_keys` with no validity check (`crypto/dkg/pedpop/src/encryption.rs:351-362`, `452-458`). `encrypt` computes the shared secret as `ecdh::<C>(&key, to)` = `to * key` (`encryption.rs:95-97`, `154`). If `to` is the identity point, the shared secret is the identity point regardless of the per-message key — the cipher key at `encryption.rs:101-133` becomes a deterministic function of only the public `context` and the public identity encoding, and the static IV `b"DKG IV v0.2\0"` is fixed (`encryption.rs:124-126`).

Compare with `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131`, which explicitly rejects identity because `Ciphersuite::read_G` does not — yet nothing rejects an identity `enc_key` inside `EncryptionKeyMessage`/`register`. Likewise, the codebase itself documents the threat model at `encryption.rs:84-90` and in `spec/cryptography/Distributed Key Generation.md` (blame proofs can reveal ECDH-derived keys), but only protects the *per-message* key with a PoP — the *static* key has no PoK/PoP and no identity check.

### Impact Explanation

The encrypted `SecretShare` an honest dealer sends to the malicious registrant is recoverable by **any** observer of the wire, not just the intended recipient. Threshold security is silently reduced by one share: a coalition of `t-1` honest/malicious parties plus this publicly-leaked share reconstructs the group secret via `recover_key` (`crypto/dkg/recovery/src/lib.rs:37-84`). Equivalently, an outsider who can merely observe DKG traffic gains share material they were never entitled to — the privilege escalation from "network observer" to "holder of threshold key material," matching the CVE's low-privilege→high-privilege shape. Because the leaked plaintext passes the share verification in `blame_internal` (`pedpop/src/lib.rs:596-604`), no fault is attributed; the leak is undetectable inside the protocol.

### Likelihood Explanation

A participant in any PedPoP session needs only to craft their `EncryptionKeyMessage` with an identity/known-DL `enc_key` — pure public input, one-time, deterministic success. No race, no negligible-probability hash condition. The only prerequisite is that the deployment accepts a `Participant`-indexed commitment message over an authenticated channel, which is the normal DKG flow (`verify_r1`, `pedpop/src/lib.rs:300-338`). Severity: **High** — threshold key material exposed to arbitrary third parties with no protocol-level detection.

### Recommendation

In `Encryption::register`/`Decryption::register` (`crypto/dkg/pedpop/src/encryption.rs`), reject `enc_key.is_identity()` and require a Schnorr proof of possession of the static encryption key bound to `(context, participant)` — analogous to the per-message `pop` already enforced in `pop_challenge` (`encryption.rs:302-324`) and the identity rejection in `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`). Ideally verify the PoK inside `verify_r1`'s existing `BatchVerifier` alongside the commitment PoKs (`pedpop/src/lib.rs:311-334`).

### Proof of Concept

1. Malicious participant `l` constructs `EncryptionKeyMessage { enc_key: C::G::identity(), msg: <valid Commitments> }` and broadcasts it; `register` accepts it (`encryption.rs:351-362`).
2. Honest dealer `d` calls `encryption.encrypt(rng, l, share_bytes)` (`encryption.rs:460-467`), producing `EncryptedMessage` with `key = kG`, `msg = share ⊕ cipher(context, ecdh(k, identity))` = `share ⊕ cipher(context, identity)`.
3. Observer `e` reads the wire message, computes `cipher::<C>(context, &Zeroizing::new(C::G::identity()))` via the deterministic transcript at `encryption.rs:101-133` and XORs it against `msg`, recovering `share_bytes` — a valid share for participant `l`, verifiable against `commitments[l]` via `share_verification_statements` (`pedpop/src/lib.rs:596-604`).
4. With `t-1` further shares, `recover_key` yields the group secret.

Uncertain: whether the surrounding network layer rejects identity points before `register` is called — that code was not visible in the inspected snippets — but nothing in the in-scope PedPoP crate performs the check.