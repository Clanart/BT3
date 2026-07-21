### Title
Hardcoded Test Private Key Used in Production `create_signature_manager()` Signs Precommit Block-Hash Votes — (`File: crates/apollo_signature_manager/src/lib.rs`)

---

### Summary

`LocalKeyStore::new_for_testing()` embeds a well-known ECDSA private key directly in source code. The production factory function `create_signature_manager()` unconditionally calls `SignatureManager::new()`, which calls `LocalKeyStore::new_for_testing()`. Every sequencer node that enables the `signature_manager` component therefore signs consensus precommit votes over block hashes with the same publicly known private key `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133`.

---

### Finding Description

**Root cause — hardcoded key wired into the production path:**

`LocalKeyStore::new_for_testing()` is marked `pub(crate)` and contains a compile-time constant private key:

```rust
// crates/apollo_signature_manager/src/signature_manager.rs
pub(crate) const fn new_for_testing() -> Self {
    const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
        "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
    ));
    ...
}
``` [1](#0-0) 

The public `SignatureManager` type alias and its constructor call this function unconditionally:

```rust
// crates/apollo_signature_manager/src/lib.rs
impl LocalKeyStoreSignatureManager {
    pub fn new() -> Self {
        Self(GenericSignatureManager::new(LocalKeyStore::new_for_testing()))
    }
}
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
``` [2](#0-1) 

The TODO comment on line 39 explicitly acknowledges that no production key-management path exists yet.

**Production wiring — node component creation:**

`create_node_components()` calls `create_signature_manager()` whenever the component is enabled (`LocalExecutionWithRemoteDisabled` or `LocalExecutionWithRemoteEnabled`):

```rust
// crates/apollo_node/src/components.rs
let signature_manager = match config.components.signature_manager.execution_mode {
    ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
    | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
        Some(create_signature_manager())
    }
    ...
};
``` [3](#0-2) 

The distributed deployment configuration (`signature_manager.json`) sets `execution_mode` to `LocalExecutionWithRemoteEnabled`, confirming this path is active in the intended production topology. [4](#0-3) 

**What the key signs — precommit votes over block hashes:**

`SignatureManager::sign_precommit_vote` signs a BLAKE2s digest of the domain separator `"PRECOMMIT_VOTE"` concatenated with the block hash bytes:

```rust
pub async fn sign_precommit_vote(
    &self,
    block_hash: BlockHash,
) -> SignatureManagerResult<RawSignature> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    self.sign(message_digest).await
}
``` [5](#0-4) 

The `ConsensusManager` receives the `signature_manager_client` and uses it to sign precommit votes that drive the Tendermint-style consensus to finalize a specific block hash. [6](#0-5) 

---

### Impact Explanation

Because the private key is embedded in public source code, any party can:

1. Derive the corresponding public key `0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a`.
2. Produce a valid ECDSA precommit-vote signature for **any** block hash of their choosing.
3. Since every deployed sequencer node uses the **same** key, all validator identities are identical and all their signatures are forgeable. An attacker can therefore manufacture a full quorum of precommit signatures for a fabricated block hash without controlling any real node.

This causes the consensus layer to bind the wrong block hash commitment, which propagates a wrong global state root, wrong block commitment, and wrong proof inputs downstream — matching the **High** impact: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

---

### Likelihood Explanation

The private key is in a public repository, readable by anyone who clones the code. No special access, network position, or cryptographic attack is required. The trigger is purely passive (read the source, compute signatures offline). Likelihood is **High**.

---

### Recommendation

1. Remove `LocalKeyStore::new_for_testing()` from any non-`#[cfg(test)]` path. Gate it with `#[cfg(any(test, feature = "testing"))]`.
2. Implement a production `KeyStore` that loads the private key from an HSM, environment secret, or encrypted key file at startup — never from a compile-time constant.
3. Resolve the `TODO(Elin)` comment before any production deployment.
4. Add a CI lint or integration-test assertion that `create_signature_manager()` panics (or fails to compile) when built without the `testing` feature and no external key source is configured.

---

### Proof of Concept

```
# 1. Extract the known private key from source:
PRIV=0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133

# 2. For any target block hash H, compute the message digest:
#    digest = blake2s("PRECOMMIT_VOTE" || H.to_bytes_be())

# 3. Sign with the known key using starknet-crypto ecdsa_sign:
#    sig = ecdsa_sign(PRIV, digest)

# 4. The resulting (r, s) is indistinguishable from a legitimate
#    precommit vote from any deployed sequencer node, because all
#    nodes share the same key.  Inject this signature into the
#    consensus network for a fabricated block hash to drive
#    consensus toward a wrong block commitment.
```

The test vectors in `signature_manager_test.rs` confirm the key produces deterministic, verifiable signatures (`ALICE_PRECOMMIT_SIGNATURE`) — an attacker can reproduce these offline for any block hash. [7](#0-6)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L63-69)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
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

**File:** crates/apollo_signature_manager/src/lib.rs (L17-43)
```rust
impl LocalKeyStoreSignatureManager {
    pub fn new() -> Self {
        Self(GenericSignatureManager::new(LocalKeyStore::new_for_testing()))
    }
}

impl Default for LocalKeyStoreSignatureManager {
    fn default() -> Self {
        Self::new()
    }
}

impl Deref for LocalKeyStoreSignatureManager {
    type Target = GenericSignatureManager<LocalKeyStore>;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

pub use LocalKeyStoreSignatureManager as SignatureManager;

// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
```

**File:** crates/apollo_node/src/components.rs (L199-227)
```rust
            let signature_manager_client = clients
                .get_signature_manager_shared_client()
                .expect("Signature Manager client should be available");
            let l1_gas_price_client = clients
                .get_l1_gas_price_shared_client()
                .expect("L1 gas price client should be available");
            let config_manager_client = clients
                .get_config_manager_shared_client()
                .expect("Config Manager client should be available");
            let proof_manager_client = clients
                .get_proof_manager_shared_client()
                .expect("Proof Manager client should be available");
            let committee_provider = create_committee_provider(
                consensus_manager_config,
                batcher_client.clone(),
                state_sync_client.clone(),
                config_manager_client.clone(),
            );
            Some(ConsensusManager::new(ConsensusManagerArgs {
                config: consensus_manager_config.clone(),
                batcher_client,
                state_sync_client,
                class_manager_client,
                signature_manager_client,
                config_manager_client,
                l1_gas_price_provider: l1_gas_price_client,
                proof_manager_client,
                committee_provider,
            }))
```

**File:** crates/apollo_node/src/components.rs (L517-523)
```rust
    let signature_manager = match config.components.signature_manager.execution_mode {
        ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
        | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
            Some(create_signature_manager())
        }
        ReactiveComponentExecutionMode::Disabled | ReactiveComponentExecutionMode::Remote => None,
    };
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

**File:** crates/apollo_signature_manager/src/signature_manager_test.rs (L27-32)
```rust
const ALICE_PRECOMMIT_SIGNATURE: Signature = Signature {
    r: Felt::from_hex_unchecked("0xe16ecc38c135735e8aed7ffdb150ebb956a93ec19ac53e8295cdbd04d552b2"),
    s: Felt::from_hex_unchecked(
        "0x4de081a9459b0e7defc49f7166f8869b33313020a20ffcc97506b8df6c42a7b",
    ),
};
```
