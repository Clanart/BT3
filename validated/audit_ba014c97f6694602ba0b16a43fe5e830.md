Now I have all the code I need to analyze this claim thoroughly. Let me trace through the exact logic.

### Title
Cross-Height Endorsement Field-Mixing Bypasses Dedup Guard, Silently Suppresses Legitimate Endorsements — (`chain/client/src/stateless_validation/chunk_endorsement.rs`, `core/primitives/src/stateless_validation/chunk_endorsement.rs`)

---

### Summary

`ChunkEndorsementV2` signs `inner` (containing `chunk_hash`) and `metadata` (containing `height_created`) as two **independent** byte blobs with no cross-binding. An unprivileged peer who has observed two genuine endorsements from the same validator V — one at height H, one at H+1 on the same shard/epoch — can splice the `inner`+`signature` from the H endorsement with the `metadata`+`metadata_signature` from the H+1 endorsement. The resulting mixed message passes every production validation check, is stored in `ChunkEndorsementTracker`'s LRU cache under key `(shard_id, epoch_id, H+1)` with `chunk_hash_A` (wrong hash), and causes the legitimate H+1 endorsement to be silently dropped by the dedup guard. `collect_chunk_endorsements` then filters out the poisoned entry because its stored `chunk_hash` does not match the real chunk header, so validator V's stake is never counted.

---

### Finding Description

**Root cause — no cross-binding between `inner` and `metadata`.**

`ChunkEndorsementV2` has four fields:

```
inner:              ChunkEndorsementInnerV1  { chunk_hash, signature_differentiator }
signature:          Signature   // signs borsh(inner) only
metadata:           ChunkEndorsementMetadata { account_id, shard_id, epoch_id, height_created }
metadata_signature: Signature   // signs borsh(metadata) only
``` [1](#0-0) 

`verify()` checks the two signatures independently:

```rust
fn verify(&self, public_key: &PublicKey) -> bool {
    let inner    = borsh::to_vec(&self.inner).unwrap();
    let metadata = borsh::to_vec(&self.metadata).unwrap();
    self.signature.verify(&inner, public_key)
        && self.metadata_signature.verify(&metadata, public_key)
}
``` [2](#0-1) 

There is no signature that commits to both `chunk_hash` and `height_created` simultaneously. This means any two independently-valid `(inner, sig)` and `(metadata, metadata_sig)` pairs from the same key can be freely recombined.

**Attack construction.**

Let the attacker observe:
- **Endorsement A** (height H): `{inner_A=(chunk_hash_A), sig_A, metadata_A=(V, shard, epoch, H), meta_sig_A}`
- **Endorsement B** (height H+1): `{inner_B=(chunk_hash_B), sig_B, metadata_B=(V, shard, epoch, H+1), meta_sig_B}`

Craft **mixed endorsement M**:
- `inner = inner_A`, `signature = sig_A`
- `metadata = metadata_B`, `metadata_signature = meta_sig_B`

`M.verify(V.pubkey)` returns `true` because both component signatures are individually valid.

**Validation path for M.**

`validate_chunk_endorsement` derives the `ChunkProductionKey` from `metadata` (H+1), confirms V is a chunk validator at that key, then calls `validate_chunk_endorsement_signature` which calls `endorsement.verify(V.pubkey)` — passes. [3](#0-2) [4](#0-3) 

**Cache poisoning.**

`process_chunk_endorsement` stores the result:

```rust
cache.get_or_insert_mut(key, || HashMap::new()).insert(
    account_id.clone(),
    (endorsement.chunk_hash(), endorsement.signature()),
);
``` [5](#0-4) 

- `key` = `(shard, epoch, H+1)` — from `metadata_B`
- stored `chunk_hash` = `chunk_hash_A` — from `inner_A` (wrong)
- stored `signature` = `sig_A` — signature over `chunk_hash_A`

**Dedup guard drops the legitimate endorsement.**

When the real endorsement B arrives:

```rust
if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
    return Ok(());   // silently dropped
}
``` [6](#0-5) 

The key `(shard, epoch, H+1)` already contains V, so the legitimate endorsement is discarded without error.

**Endorsement collection fails.**

`collect_chunk_endorsements` filters cached entries by `chunk_hash`:

```rust
.filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
``` [7](#0-6) 

V's cached entry holds `chunk_hash_A` ≠ `chunk_hash_B`, so V's stake is excluded from the endorsement tally. If enough validators are targeted, `is_endorsed` becomes `false` and the chunk cannot be included in a block.

---

### Impact Explanation

An unprivileged network peer who can observe two genuine `ChunkEndorsementV2` messages from the same validator V (at heights H and H+1 on the same shard) can permanently suppress V's endorsement for the H+1 chunk within the block producer's LRU cache. If the attacker targets enough validators to drop the endorsement below the required stake threshold, the chunk at H+1 is never endorsed and cannot be included in a block — a targeted liveness failure for that shard. The block producer receives no error; the suppression is silent.

---

### Likelihood Explanation

Chunk endorsements are P2P messages routed through the gossip network. A peer that is on the routing path between chunk validators and the block producer, or that receives relayed endorsements, can observe the required messages. The attacker needs only two endorsements from the same validator on the same shard across consecutive heights — a condition that is met in every epoch for every active chunk validator. No validator key material is required; only passive observation and message replay.

---

### Recommendation

Bind `inner` and `metadata` under a single signature. The simplest fix is to sign a combined struct:

```rust
struct ChunkEndorsementSignedData {
    chunk_hash:     ChunkHash,
    shard_id:       ShardId,
    epoch_id:       EpochId,
    height_created: BlockHeight,
    signature_differentiator: SignatureDifferentiator,
}
```

and produce exactly one signature over its Borsh serialization. The separate `metadata_signature` field should be removed. `verify()` must then check the single combined signature, making it impossible to mix fields from endorsements at different heights. [8](#0-7) 

---

### Proof of Concept

A unit test in `chain/client/src/stateless_validation/chunk_endorsement.rs`:

1. Create a `ValidatorSigner` for validator V.
2. Produce **endorsement A** for `chunk_hash_A` at height H using `ChunkEndorsement::new`.
3. Produce **endorsement B** for `chunk_hash_B` at height H+1 using `ChunkEndorsement::new`.
4. Destructure both into their `ChunkEndorsementV2` fields and construct **mixed endorsement M**:
   - `inner` = A's inner (chunk_hash_A), `signature` = A's signature
   - `metadata` = B's metadata (H+1), `metadata_signature` = B's metadata_signature
5. Assert `M.verify(V.pubkey())` returns `true`.
6. Call `tracker.process_chunk_endorsement(&M)` — expect `Ok(())`.
7. Call `tracker.process_chunk_endorsement(&B)` — expect `Ok(())` (silently dropped by dedup).
8. Call `tracker.collect_chunk_endorsements(chunk_header_B)` and assert `is_endorsed == false` even though V is a chunk validator with sufficient stake.

The test will pass (demonstrating the bug) because step 6 poisons the cache and step 7 is silently discarded.

### Citations

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L22-38)
```rust
impl ChunkEndorsement {
    pub fn new(
        epoch_id: EpochId,
        chunk_header: &ShardChunkHeader,
        signer: &ValidatorSigner,
    ) -> ChunkEndorsement {
        let metadata = ChunkEndorsementMetadata {
            account_id: signer.validator_id().clone(),
            shard_id: chunk_header.shard_id(),
            epoch_id,
            height_created: chunk_header.height_created(),
        };
        let metadata_signature = signer.sign_bytes(&borsh::to_vec(&metadata).unwrap());
        let inner = ChunkEndorsementInnerV1::new(chunk_header.chunk_hash().clone());
        let signature = signer.sign_bytes(&borsh::to_vec(&inner).unwrap());
        let endorsement = ChunkEndorsementV2 { inner, signature, metadata, metadata_signature };
        ChunkEndorsement::V2(endorsement)
```

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L100-118)
```rust
pub struct ChunkEndorsementV2 {
    // This is the part of the chunk endorsement that signed and included in the block header
    inner: ChunkEndorsementInnerV1,
    // This is the signature of the inner field, to be included in the block header
    signature: Signature,
    // This consists of the metadata for chunk endorsement used in validation
    metadata: ChunkEndorsementMetadata,
    // Metadata signature is used to validate that the metadata is produced by the expected validator
    metadata_signature: Signature,
}

impl ChunkEndorsementV2 {
    fn verify(&self, public_key: &PublicKey) -> bool {
        let inner = borsh::to_vec(&self.inner).unwrap();
        let metadata = borsh::to_vec(&self.metadata).unwrap();
        self.signature.verify(&inner, public_key)
            && self.metadata_signature.verify(&metadata, public_key)
    }
}
```

**File:** chain/client/src/stateless_validation/validate.rs (L308-332)
```rust
pub fn validate_chunk_endorsement(
    epoch_manager: &dyn EpochManagerAdapter,
    endorsement: &ChunkEndorsement,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let _span = tracing::debug_span!(
        target: "stateless_validation",
        "validate_chunk_endorsement",
        height = endorsement.chunk_production_key().height_created,
        shard_id = %endorsement.chunk_production_key().shard_id,
        validator = %endorsement.account_id(),
        tag_block_production = true
    )
    .entered();

    require_relevant!(validate_chunk_relevant_as_validator(
        epoch_manager,
        &endorsement.chunk_production_key(),
        endorsement.account_id(),
        store,
    )?);
    validate_chunk_endorsement_signature(epoch_manager, endorsement)?;

    Ok(ChunkRelevance::Relevant)
}
```

**File:** chain/client/src/stateless_validation/validate.rs (L492-504)
```rust
fn validate_chunk_endorsement_signature(
    epoch_manager: &dyn EpochManagerAdapter,
    endorsement: &ChunkEndorsement,
) -> Result<(), Error> {
    let validator = epoch_manager.get_validator_by_account_id(
        &endorsement.chunk_production_key().epoch_id,
        &endorsement.account_id(),
    )?;
    if !endorsement.verify(validator.public_key()) {
        return Err(Error::InvalidChunkEndorsement);
    }
    Ok(())
}
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L49-53)
```rust
            let cache = self.chunk_endorsements.lock();
            if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
                tracing::debug!(target: "client", ?endorsement, "already received chunk endorsement");
                return Ok(());
            }
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L59-63)
```rust
                let mut cache = self.chunk_endorsements.lock();
                cache.get_or_insert_mut(key, || HashMap::new()).insert(
                    account_id.clone(),
                    (endorsement.chunk_hash(), endorsement.signature()),
                );
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L107-111)
```rust
        let validator_signatures = entry
            .into_iter()
            .filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
            .map(|(account_id, (_, signature))| (account_id, signature.clone()))
            .collect();
```
