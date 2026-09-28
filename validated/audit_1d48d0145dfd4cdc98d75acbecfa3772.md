### Title
Threshold secret shares serialized and persisted in plaintext - (File: crypto/dkg/src/lib.rs)

### Summary
The Jenkins Crowd plugin advisory (CWE-522) concerns credentials written to disk unencrypted, readable by anyone with access to the file system. The direct Serai analog is `ThresholdKeys::write`/`serialize` in `crypto/dkg`, which writes `self.core.secret_share` — the FROST secret key share — as a raw unencrypted scalar. The processor then persists these serialized keys into the database via `GeneratedKeysDb::save_keys` / `KeysDb` (`processor/src/key_gen.rs`), meaning the validator's threshold secret share sits in a general-purpose database in plaintext.

This is notable because Serai's own code acknowledges the DB "isn't a proper secret store": in `coordinator/src/tributary/signing_protocol.rs` the cached FROST preprocess is XOR-masked with a key derived from the validator's private key before being written to `CachedPreprocesses`, precisely because "recovery of it will enable recovering the private key" and "the DB isn't expected to be arbitrarily readable... shouldn't be trusted as one". The far more sensitive `ThresholdKeys` secret share receives no such protection.

### Finding Description
`ThresholdKeys::write` serializes the secret share directly:
- `crypto/dkg/src/lib.rs:553-555` — `let mut share_bytes = self.core.secret_share.to_repr(); writer.write_all(share_bytes.as_ref())?;`
- `ThresholdKeys::read` (`lib.rs:618`) reads it back with `C::read_F`.

The processor stores these bytes unencrypted:
- `processor/src/key_gen.rs:65-84` — `save_keys` concatenates `substrate_keys.serialize()` / `network_keys.serialize()` and `txn.put`s them into `GeneratedKeysDb`.
- `KeysDb` (`key_gen.rs:39`) maps `network_key -> Vec<u8>` (the serialized `ThresholdKeys`), keyed only by the public group key — so the DB entry is enumerable by a public value.

Contrast with `coordinator/src/tributary/signing_protocol.rs:104-143`, where the cached preprocess seed (equivalent sensitivity) is explicitly encrypted before DB storage with commentary explaining the DB must not be trusted as a secret store.

### Impact Explanation
Any party able to read the processor's database (filesystem access, DB snapshot/backup, container volume, log shipping of the DB directory) recovers the full threshold secret share `core.secret_share` in cleartext — no decryption needed. Per the FROST spec in `spec/cryptography/FROST.md`, knowledge of the share is equivalent to the private key share itself. Combined with t−1 other compromised shares, or used to mount blame/share-recovery flows, this collapses the confidentiality of the validator's key material. This is the identical exposure class as the Jenkins advisory: a secret persisted in plaintext where lower-privilege readers of the same host can reach it.

### Likelihood Explanation
The plaintext form is persistent and unconditional: every successful key generation writes shares via `save_keys`, and `KeysDb` retains them under a publicly-known key. Attack prerequisites are host/DB read access rather than protocol participation, matching the original advisory's "users with access to the master file system" model.

### Recommendation
Encrypt `ThresholdKeys` before persistence, as is already done for `CachedPreprocess`: derive a storage key (e.g., Blake2s256 over a domain separator ‖ context ‖ validator private key, or an AEAD over the serialized bytes) and only write ciphertext to `GeneratedKeysDb`/`KeysDb`. Alternatively, document and enforce that the processor DB must be an encrypted secret store. At minimum the inconsistency — encrypting the preprocess seed but not the share itself — should be resolved.

### Proof of Concept
1. Complete any DKG in the processor; `KeyGenDb::handle` calls `GeneratedKeysDb::save_keys` (`processor/src/key_gen.rs:494`).
2. Read the DB entry for `GeneratedKeysDb::key(session, substrate_key_bytes, network_key_bytes)` or `KeysDb::key(network_group_key_bytes)`.
3. Feed the value to `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`); the `secret_share` field deserializes to the valid scalar share with no key material required — the bytes after the interpolation header are the raw `to_repr()` of the share (`lib.rs:553-554`).
4. Verify against `original_verification_share(params.i())`: `G * recovered_share == verification_shares[i]` confirms the recovered secret share is authentic.