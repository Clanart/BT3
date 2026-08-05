[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** gossip/src/duplicate_shred.rs (L169-171)
```rust
///     - If `shred1` and `shred2` do not share the same index and are data shreds
///       verify that they indicate an index conflict. One of them must be the
///       LAST_SHRED_IN_SLOT, however the other shred must have a higher index.
```

**File:** gossip/src/duplicate_shred.rs (L183-221)
```rust
    if shred1.slot() != shred2.slot() {
        return Err(Error::SlotMismatch);
    }

    if shred1.version() != shred_version {
        return Err(Error::InvalidShredVersion(shred1.version()));
    }
    if shred2.version() != shred_version {
        return Err(Error::InvalidShredVersion(shred2.version()));
    }

    if let Some(leader_schedule) = leader_schedule {
        let slot_leader =
            leader_schedule(shred1.slot()).ok_or(Error::UnknownSlotLeader(shred1.slot()))?;
        if !shred1.verify(&slot_leader) || !shred2.verify(&slot_leader) {
            return Err(Error::InvalidSignature);
        }
    }

    // Merkle root conflict check
    if shred1.fec_set_index() == shred2.fec_set_index()
        && shred1.merkle_root().ok() != shred2.merkle_root().ok()
    {
        // This catches a mixture of legacy and merkle shreds
        // as well as merkle shreds with different roots in the
        // same fec set
        return Ok(());
    }

    if shred1.shred_type() != shred2.shred_type() {
        return Err(Error::ShredTypeMismatch);
    }

    if shred1.index() == shred2.index() {
        if shred1.is_shred_duplicate(shred2) {
            return Ok(());
        }
        return Err(Error::InvalidDuplicateShreds);
    }
```

**File:** gossip/src/duplicate_shred.rs (L223-230)
```rust
    if shred1.shred_type() == ShredType::Data {
        if shred1.last_in_slot() && shred2.index() > shred1.index() {
            return Ok(());
        }
        if shred2.last_in_slot() && shred1.index() > shred2.index() {
            return Ok(());
        }
        return Err(Error::InvalidLastIndexConflict);
```
