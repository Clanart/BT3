### Title
Threshold key shares written unencrypted to the processor's on-disk database, recoverable by any local reader - (File: processor/src/key_gen.rs)

### Summary
CVE-2022-48319 concerns a sensitive secret (the Checkmk host secret) being written into an unprotected local file (`cmk-update-agent.log`) readable by an unprivileged local party (CVSS 5.5, `AV:L ... C:H`). The Serai analog is the DKG output path in `processor/src/key_gen.rs`, which serializes `ThresholdKeys` — including each validator's FROST `secret_share` — and stores them in the processor's database via a plain `txn.put`, with no encryption or access-control layer in the DB abstraction.

### Finding Description
After PedPoP key generation completes, `GeneratedKeysDb::save_keys` serializes every `ThresholdKeys<Ristretto>` and `ThresholdKeys<N::Curve>` into a buffer and persists it verbatim: [1](#0-0) . `KeysDb::confirm_keys` then copies the same plaintext blob into the long-lived `KeysDb` entry keyed by the network public key: [2](#0-1) . The serialized form necessarily contains `ThresholdCore::secret_share` (the field exists at `crypto/dkg/src/lib.rs:263` and is what `read_keys` reconstructs via `ThresholdKeys::read` for signing after reboot): [3](#0-2) . The `create_db!` backends (`common/db/src/parity_db.rs`, `common/db/src/rocks.rs`) expose only raw `get`/`put` byte storage — a grep across `common/db/**` shows no encryption, key-wrap, or permission-hardening layer. Like the Checkmk log file, the secret bytes sit in a persistent file whose confidentiality depends entirely on filesystem defaults.

### Impact Explanation
Any local user or co-resident process able to read the processor's database directory recovers the validator's threshold secret share in cleartext — a direct confidentiality loss of key material, matching the CVE's `C:H` impact. A single share doesn't alone forge signatures, but combined with shares taken from t compromised validators it enables full group-key recovery / share reconstruction (`Interpolation::Constant`/`Lagrange` in `crypto/dkg/src/lib.rs`), i.e., key share recovery as required by the analog rules.

### Likelihood Explanation
Reachability mirrors the CVE: this is a local disclosure channel (`AV:L`), not remotely triggerable, and it requires no user interaction. The processor writes the keys unconditionally on every successful DKG (`CoordinatorMessage::Shares` handling → `confirm_keys`), so the exposure exists by default on every validator. Severity is accordingly Medium rather than High — the attacker still needs local read access to the DB file.

### Recommendation
Encrypt `GeneratedKeysDb`/`KeysDb` values at rest (e.g., a per-node AEAD key sealed to the validator's entropy/OS keyring, mirroring the `cipher()` construction already used for DKG transcripts in `crypto/dkg/pedpop/src/encryption.rs:101`), or store shares in a hardened key store rather than the general-purpose parity/rocks DB. At minimum, document and enforce restrictive file permissions on the database directory, since the current code provides no protection at all.

### Proof of Concept
1. Run a processor through `CoordinatorMessage::GenerateKey` → `Commitments` → `Shares` so `confirm_keys` executes.
2. As any local user able to open the processor's DB path, iterate the `KeysDb`/`GeneratedKeysDb` entries (`GeneratedKeysDb::key(&session, &substrate_key, &network_key)`), read the stored `Vec<u8>`.
3. Feed the blob to `ThresholdKeys::<Ristretto>::read(&mut bytes)` — succeeds and reconstructs the full `ThresholdCore`, exposing `secret_share` without any decryption step, exactly as `GeneratedKeysDb::read_keys` does internally at `processor/src/key_gen.rs:57`.

### Citations

**File:** processor/src/key_gen.rs (L65-84)
```rust
  fn save_keys<N: Network>(
    txn: &mut impl DbTxn,
    id: &KeyGenId,
    substrate_keys: &[ThresholdKeys<Ristretto>],
    network_keys: &[ThresholdKeys<N::Curve>],
  ) {
    let mut keys = Zeroizing::new(vec![]);
    for (substrate_keys, network_keys) in substrate_keys.iter().zip(network_keys) {
      keys.extend(substrate_keys.serialize().as_slice());
      keys.extend(network_keys.serialize().as_slice());
    }
    txn.put(
      Self::key(
        &id.session,
        &substrate_keys[0].group_key().to_bytes(),
        network_keys[0].group_key().to_bytes().as_ref(),
      ),
      keys,
    );
  }
```

**File:** processor/src/key_gen.rs (L106-108)
```rust
    txn.put(Self::key(key_pair.1.as_ref()), keys_vec);
    NetworkKeyDb::set(txn, session, &key_pair.1.clone().into_inner());
    SessionDb::set(txn, key_pair.1.as_ref(), &session);
```

**File:** crypto/dkg/src/lib.rs (L257-264)
```rust
#[derive(Clone, PartialEq, Eq)]
struct ThresholdCore<C: Ciphersuite> {
  params: ThresholdParams,
  group_key: C::G,
  verification_shares: HashMap<Participant, C::G>,
  interpolation: Interpolation<C::F>,
  secret_share: Zeroizing<C::F>,
}
```
