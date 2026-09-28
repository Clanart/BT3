### Title
Threshold secret shares stored in plaintext in the processor database (`KeysDb` / `GeneratedKeysDb`) - (File: crypto/dkg/src/lib.rs, processor/src/key_gen.rs)

### Summary
Analogous to Navidrome storing its JWT secret in plaintext in `navidrome.db` (CWE-312), Serai's processor persists `ThresholdKeys` — which contain the validator's FROST `secret_share` — to its database completely unencrypted. The same codebase explicitly encrypts cached signing preprocesses before writing them to the DB because "the DB isn't expected to be arbitrarily readable... isn't a proper secret store and shouldn't be trusted as one" (`coordinator/src/tributary/signing_protocol.rs:104-106`), yet the far more sensitive long-term secret shares are written raw.

### Finding Description
`ThresholdKeys::write` serializes the secret share directly:

- `crypto/dkg/src/lib.rs:553-554`: `let mut share_bytes = self.core.secret_share.to_repr(); writer.write_all(share_bytes.as_ref());` — the FROST secret share scalar is appended in cleartext.
- `crypto/dkg/src/lib.rs:567-571`: `serialize()` returns these bytes, and `read()` (`crypto/dkg/src/lib.rs:574-632`) recovers the share via `C::read_F` at line 618 with no decryption step.

These serialized keys are then stored unencrypted:

- `processor/src/key_gen.rs:65-84`: `GeneratedKeysDb::save_keys` concatenates `substrate_keys.serialize()` and `network_keys.serialize()` and writes them via `txn.put` into `GeneratedKeysDb`.
- `processor/src/key_gen.rs:106`: `KeysDb::confirm_keys` copies the same plaintext blob into `KeysDb`, keyed by network key, where it persists for the lifetime of the validator set and is read back on every signing operation (`KeysDb::keys`, `processor/src/key_gen.rs:113-121`).

Contrast with `coordinator/src/tributary/signing_protocol.rs:104-143`, where a mere cached preprocess (a nonce whose reuse across two signatures would leak the key) is XOR-masked with a key derived from `Blake2s256("Cached Preprocess Encryption Key" || context || secret)` before being written to `CachedPreprocesses`. The threat model — DB contents readable by an attacker — is explicitly acknowledged, but the protection is only applied to the preprocess cache, not to `GeneratedKeysDb`/`KeysDb`.

### Impact Explanation
Anyone who obtains read access to a validator's processor database file (disk image, backup, unprivileged local process, log/dump of the DB) directly recovers that validator's threshold secret share(s) for both the substrate (Ristretto) and network keys — i.e., key share recovery with zero cryptographic work. Combined with shares from `t - 1` other compromised validators' databases, an attacker reconstructs the full group private key and can forge signatures draining Serai-managed funds. Even below threshold, a recovered share is a direct contribution to the secret polynomial and materially lowers the attacker's work versus the intended security model. Unlike the Navidrome case, a single share does not alone permit forgery, but the stored material is the system's most critical secret and is protected only by filesystem permissions — exactly the "not a proper secret store" condition the coordinator code already warns about.

### Likelihood Explanation
The plaintext blob is permanent: once `GeneratedKeysDb`/`KeysDb` are written, the share persists on disk for the entire set lifetime and across reboots, unlike in-memory `Zeroizing` copies. Any DB exfiltration — backup theft, container/volume snapshot, compromise of the host by a lower-privileged process — yields the share. An attacker need not break any cryptographic assumption; the reachability is simply file readability, which is precisely the precondition of the referenced CVE (CVSS `AV:L`). Because Serai validators are long-lived processes with durable DBs, and operators commonly snapshot or back up disk state, exposure of even a threshold subset of validators' DBs is a realistic compromise path.

### Recommendation
Apply the same defense already used for `CachedPreprocesses`: encrypt (or at minimum AEAD/XOR-mask with a secret-derived key) the `secret_share` portion of `ThresholdKeys` before persisting via `GeneratedKeysDb::save_keys` / `KeysDb::confirm_keys` in `processor/src/key_gen.rs`. Options:

- Wrap the serialized key material with an encryption key derived from the validator's private key / entropy plus a domain separator (e.g., `Blake2s256("ThresholdKeys Encryption Key" || context || secret)`), mirroring `preprocess_internal`.
- Alternatively, store `secret_share` under a separate, properly protected secret store and keep only public parameters in the DB.
- On read (`GeneratedKeysDb::read_keys`), decrypt before calling `ThresholdKeys::read`.

### Proof of Concept
1. A validator completes DKG: `CoordinatorMessage::Shares` handling reaches `GeneratedKeysDb::save_keys::<N>(txn, &id, &substrate_keys, &network_keys)` (`processor/src/key_gen.rs:494`), then `KeysDb::confirm_keys` writes the same bytes under `KeysDb::key(network_key)` (`processor/src/key_gen.rs:106`).
2. Attacker reads the DB file. The value at the `KeysDb` entry is a concatenation of `ThresholdKeys::serialize()` outputs. Per `ThresholdKeys::write` (`crypto/dkg/src/lib.rs:538-561`), after the curve ID, `t`, `n`, `i`, and the interpolation tag, the next `F::Repr` bytes are the raw `secret_share` scalar — no encryption, MAC, or obfuscation.
3. Attacker parses it with `ThresholdKeys::<C>::read` (`crypto/dkg/src/lib.rs:574-632`), which returns a fully functional `ThresholdKeys` whose `core.secret_share` can sign preprocesses/shares as that participant.
4. Repeating for `t` distinct validators' DBs yields `t` valid shares; standard Lagrange interpolation over the participant indexes reconstructs the group private key, enabling arbitrary signature forgery — the exact impact class (secret share recovery enabling forgery) of the Navidrome plaintext-secret advisory.