### Title
Hardcoded Private Key in Production `create_signature_manager()` Enables Forged Consensus Precommit Vote Signatures - (File: crates/apollo_signature_manager/src/lib.rs)

### Summary
The production function `create_signature_manager()` unconditionally instantiates `LocalKeyStore::new_for_testing()`, which embeds a well-known private key directly in source code. Any party who reads the repository can reproduce the ECDSA signing key and forge valid precommit-vote signatures over arbitrary block hashes, breaking the BFT consensus integrity invariant.

### Finding Description
`LocalKeyStore::new_for_testing()` hard-codes a Stark-curve private key:

```rust
// crates/apollo_signature_manager/src/signature_manager.rs, lines 96-106
pub(crate) const fn new_for_testing() -> Self {
    const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
        "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
    ));
    const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
        "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
    ));
    Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
}
``` [1](#0-0) 

The production factory function `create_signature_manager()` calls this directly:

```rust
// crates/apollo_signature_manager/src/lib.rs, lines 41-43
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()   // → LocalKeyStore::new_for_testing()
}
``` [2](#0-1) 

The `SignatureManager` uses this key for two operations:

1. `sign_precommit_vote(block_hash)` — produces the ECDSA signature that BFT consensus nodes attach to their precommit votes over a block hash.
2. `identify(peer_id, nonce)` — produces the ECDSA signature used to authenticate the sequencer's P2P identity. [3](#0-2) 

The accompanying TODO comment confirms no production key-management path exists yet:

```
// TODO(Elin): understand how key store would look in production and better define
// the way the signature manager is created.
``` [4](#0-3) 

### Impact Explanation
Because the private key is a compile-time constant in a public repository, any observer can:

1. Reconstruct the key: `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133`.
2. Call `ecdsa_sign(private_key, build_precommit_vote_message_digest(arbitrary_block_hash))` to produce a valid signature over **any** block hash.
3. Inject forged precommit votes into the BFT consensus round, causing honest validators to accept a wrong block commitment as finalized.

This directly corrupts the block-hash/commitment invariant: the wrong block hash (and therefore wrong state root, wrong receipt root, wrong event commitment) is accepted as authoritative. It maps to:

> **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

and potentially:

> **Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result** — if the forged commitment propagates to storage and proof inputs.

### Likelihood Explanation
The key is a `pub(crate) const` in a Rust source file committed to the repository. No runtime secret injection, no environment variable, no key-management service stands between the attacker and the key. Any developer, CI runner, or external auditor who clones the repository immediately possesses the signing key. Likelihood is **High**.

### Recommendation
1. Remove `LocalKeyStore::new_for_testing()` from all production call sites. Gate it behind `#[cfg(test)]` or a `testing` feature flag.
2. Implement `create_signature_manager()` to load the private key from a secret-management backend (environment variable, GCP Secret Manager, HSM) at runtime, consistent with the existing `ExternalSecret` / `Sensitive<T>` infrastructure already present in the deployment layer.
3. Rotate the exposed key: treat `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133` as permanently compromised and never use it outside isolated test environments.
4. Add a CI lint that rejects any `from_hex_unchecked` constant matching a known private-key pattern outside `#[cfg(test)]` modules.

### Proof of Concept

```
1. Clone the repository.
2. Note the constant at signature_manager.rs:98-99:
       PRIVATE_KEY = 0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133
3. Construct the precommit-vote digest for a target block hash B:
       digest = blake2s("PRECOMMIT_VOTE" || B.to_bytes_be())
4. Sign:
       (r, s) = ecdsa_sign(PRIVATE_KEY, digest)
5. Broadcast the forged (B, r, s) precommit vote to the consensus network.
6. Honest validators call verify_precommit_vote_signature(B, (r,s), PUBLIC_KEY)
   → returns Ok(true), accepting the forged commitment.
``` [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L129-136)
```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);

    MessageDigest(blake2s_to_felt(&message))
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
