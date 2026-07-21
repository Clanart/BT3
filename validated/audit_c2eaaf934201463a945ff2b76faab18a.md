### Title
Hardcoded Plaintext Validator Private Key Used in Production Signing Path — (`crates/apollo_signature_manager/src/signature_manager.rs`, `crates/apollo_signature_manager/src/lib.rs`)

---

### Summary

The production sequencer node unconditionally instantiates its `SignatureManager` with `LocalKeyStore::new_for_testing()`, which embeds a single hardcoded ECDSA private key as a compile-time constant. The `PrivateKey` type derives `Debug + Serialize + Deserialize`, and `LocalKeyStore` derives `Clone + Copy + Debug` with a `pub public_key` field, making the key trivially extractable from logs, serialized state, or process memory. This key is used to sign precommit votes over block hashes (block commitments). Any party who reads the source code, a log line, or a memory dump already possesses the key and can forge precommit votes for arbitrary block hashes.

---

### Finding Description

`create_signature_manager()` in `crates/apollo_signature_manager/src/lib.rs` is the sole production factory for the `SignatureManager` component:

```rust
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()   // → LocalKeyStoreSignatureManager::new()
                              //   → LocalKeyStore::new_for_testing()
}
``` [1](#0-0) 

`LocalKeyStore::new_for_testing()` hard-codes the private key as a `const`:

```rust
const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
));
``` [2](#0-1) 

The only other constructor, `_new(private_key: PrivateKey)`, is prefixed with an underscore and is never called from any production path. The `create_signature_manager()` factory is wired directly into the node's component initialization:

```rust
let signature_manager = match config.components.signature_manager.execution_mode {
    ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
    | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
        Some(create_signature_manager())
    }
    ...
};
``` [3](#0-2) 

The `PrivateKey` type derives `Debug + Serialize + Deserialize`:

```rust
#[derive(
    Debug, Default, derive_more::Deref, Copy, Clone, Eq, PartialEq, Hash, Deserialize, Serialize,
)]
pub struct PrivateKey(pub Felt);
``` [4](#0-3) 

`LocalKeyStore` itself derives `Clone + Copy + Debug` and exposes `pub public_key`, while `SignatureManager<KS>` has `pub keystore: KS`: [5](#0-4) [6](#0-5) 

This key is used to sign precommit votes over block hashes:

```rust
pub async fn sign_precommit_vote(&self, block_hash: BlockHash) -> SignatureManagerResult<RawSignature> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    self.sign(message_digest).await
}
``` [7](#0-6) 

A TODO comment in `lib.rs` acknowledges the production key management path is unresolved: `"TODO(Elin): understand how key store would look in production and better define the way the signature manager is created."` — yet `create_signature_manager()` is already wired into the live node. [1](#0-0) 

---

### Impact Explanation

The `SignatureManager` signs precommit votes that bind a validator's identity to a specific `BlockHash` (block commitment). Possession of the private key allows an adversary to:

1. Forge precommit votes for any block hash, including one corresponding to a wrong state root, wrong state diff commitment, or wrong transaction commitment.
2. Impersonate the sequencer node in peer identity challenges (`sign_precommit_vote` / `identify`), breaking the BFT consensus assumption that each validator controls a unique key.

Because the key is hardcoded and publicly known from the source, **every deployment shares the same signing identity**. Any node or observer can forge a valid precommit signature over an arbitrary block hash without any memory access at all.

This matches the allowed impact: **High — signature/hash logic binds the wrong signer or hash to a block commitment**.

---

### Likelihood Explanation

- The hardcoded key value (`0x608bf2...`) is visible in the public source repository.
- `PrivateKey` derives `Debug`, so any `tracing`/`log` call that formats the `SignatureManager` or `LocalKeyStore` will emit the key in plaintext to log output.
- No configuration, environment variable, or secret-store path exists to supply a different key; `_new` is dead code.
- The distributed deployment config (`signature_manager.json`) enables the signature manager as `LocalExecutionWithRemoteEnabled`, confirming it runs in production. [8](#0-7) 

---

### Recommendation

**Short term:**
- Remove `Debug` from `PrivateKey` (or implement a redacting `Debug` that prints `"[REDACTED]"`).
- Gate `LocalKeyStore::new_for_testing()` behind `#[cfg(test)]` so it cannot be called from production code.
- Add a `#[cfg(not(test))]` compile error or `panic!` in `create_signature_manager()` until a real key-loading path exists.

**Long term:**
- Implement a production `KeyStore` that loads the private key from an HSM, KMS, or encrypted secret store (e.g., the `ExternalSecret` / Kubernetes Secret infrastructure already present in the deployment layer).
- Ensure the private key is never held as a `Copy` type in long-lived memory; zero it after use.
- Add a CI lint that forbids `Debug` on any type whose name contains `PrivateKey` or `SecretKey`.

---

### Proof of Concept

The private key is already public. Any party can construct a valid precommit signature for an arbitrary block hash using the known constant:

```rust
use starknet_api::crypto::utils::PrivateKey;
use starknet_core::types::Felt;
use starknet_core::crypto::ecdsa_sign;

let private_key = PrivateKey(Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
));

// Forge a precommit vote for any block hash:
let forged_block_hash = /* attacker-chosen value */;
let message = build_precommit_vote_message_digest(forged_block_hash);
let forged_signature = ecdsa_sign(&private_key, &message).unwrap();
// forged_signature will pass verify_precommit_vote_signature() for the sequencer's public key.
``` [7](#0-6) [2](#0-1)

### Citations

**File:** crates/apollo_signature_manager/src/lib.rs (L39-43)
```rust
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L44-47)
```rust
#[derive(Clone, Debug)]
pub struct SignatureManager<KS: KeyStore> {
    pub keystore: KS,
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L63-77)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }

    async fn sign(&self, message_digest: MessageDigest) -> SignatureManagerResult<RawSignature> {
        let private_key = self.keystore.get_key().await?;
        let signature = ecdsa_sign(&private_key, &message_digest)
            .map_err(|e| SignatureManagerError::Sign(e.to_string()))?;

        Ok(signature.into())
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L84-88)
```rust
#[derive(Clone, Copy, Debug)]
pub struct LocalKeyStore {
    pub public_key: PublicKey,
    private_key: PrivateKey,
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L96-106)
```rust
    pub(crate) const fn new_for_testing() -> Self {
        // Created using `cairo-lang`.
        const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
            "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
        ));
        const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
            "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
        ));

        Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
    }
```

**File:** crates/apollo_node/src/components.rs (L517-522)
```rust
    let signature_manager = match config.components.signature_manager.execution_mode {
        ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
        | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
            Some(create_signature_manager())
        }
        ReactiveComponentExecutionMode::Disabled | ReactiveComponentExecutionMode::Remote => None,
```

**File:** crates/starknet_api/src/crypto/utils.rs (L42-45)
```rust
#[derive(
    Debug, Default, derive_more::Deref, Copy, Clone, Eq, PartialEq, Hash, Deserialize, Serialize,
)]
pub struct PrivateKey(pub Felt);
```

**File:** crates/apollo_deployments/resources/services/distributed/signature_manager.json (L92-105)
```json
  "components.signature_manager.execution_mode": "LocalExecutionWithRemoteEnabled",
  "components.signature_manager.local_server_config.#is_none": false,
  "components.signature_manager.local_server_config.high_priority_requests_channel_capacity": 1024,
  "components.signature_manager.local_server_config.inbound_requests_channel_capacity": 1024,
  "components.signature_manager.local_server_config.normal_priority_requests_channel_capacity": 1024,
  "components.signature_manager.local_server_config.processing_time_warning_threshold_ms": 3000,
  "components.signature_manager.max_concurrency": 128,
  "components.signature_manager.port": 1,
  "components.signature_manager.remote_client_config.#is_none": true,
  "components.signature_manager.remote_server_config.#is_none": false,
  "components.signature_manager.remote_server_config.bind_ip": "0.0.0.0",
  "components.signature_manager.remote_server_config.max_streams_per_connection": 8,
  "components.signature_manager.remote_server_config.set_tcp_nodelay": true,
  "components.signature_manager.url": "remote_service",
```
