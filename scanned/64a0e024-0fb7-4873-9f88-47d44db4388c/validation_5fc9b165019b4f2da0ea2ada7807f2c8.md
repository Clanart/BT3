[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** ledger/src/shred/wire.rs (L1-3)
```rust
// Helper methods to extract pieces of the shred from the payload without
// deserializing the entire payload.
#![deny(clippy::indexing_slicing)]
```

**File:** ledger/src/shred/wire.rs (L150-158)
```rust
#[inline]
fn get_data_size(shred: &[u8]) -> Result<u16, Error> {
    debug_assert_eq!(get_shred_type(shred).unwrap(), ShredType::Data);
    let Some(bytes) = shred.get(86..86 + 2) else {
        return Err(Error::InvalidPayloadSize(shred.len()));
    };
    let bytes = <[u8; 2]>::try_from(bytes).unwrap();
    Ok(u16::from_le_bytes(bytes))
}
```

**File:** ledger/src/shred/wire.rs (L160-170)
```rust
#[inline]
#[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
pub(crate) fn get_data(shred: &[u8]) -> Result<&[u8], Error> {
    match get_shred_variant(shred)? {
        ShredVariant::MerkleCode { .. } => Err(Error::InvalidShredType),
        ShredVariant::MerkleData {
            proof_size,
            resigned,
        } => shred::merkle::ShredData::get_data(shred, proof_size, resigned, get_data_size(shred)?),
    }
}
```
