[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** accounts-db/src/account_info.rs (L46-54)
```rust
#[bitfield(bits = 32)]
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
pub struct PackedOffsetAndFlags {
    /// this provides 2^31 bits, which when multiplied by 8 (sizeof(u64)) = 16G, which is the maximum size of an append vec
    offset_reduced: B31,
    /// use 1 bit to specify that the entry is zero lamport
    is_zero_lamport: bool,
}
```

**File:** bucket_map/src/index_entry.rs (L202-210)
```rust
#[bitfield(bits = 64)]
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
pub(crate) struct PackedRefCount {
    /// whether this entry in the data file is occupied or not
    pub(crate) occupied: B1,
    /// ref_count of this entry. We don't need any where near 63 bits for this value
    pub(crate) ref_count: B63,
}
```

**File:** bucket_map/src/index_entry.rs (L336-343)
```rust
/// Pack the storage offset and capacity-when-created-pow2 fields into a single u64
#[bitfield(bits = 64)]
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
struct PackedStorage {
    capacity_when_created_pow2: B8,
    offset: B56,
}
```

**File:** version/src/v4.rs (L109-113)
```rust
    const PRERELEASE_BITS_OFFSET: u32 = 14;
    const PRERELEASE_MASK_BITS: u32 = 2;
    const PRERELEASE_FIRST_UNMASKED_BIT: u16 = 1 << Self::PRERELEASE_MASK_BITS;
    const PRERELEASE_MASK: u16 = Self::PRERELEASE_FIRST_UNMASKED_BIT - 1;
    const PRERELEASE_MINOR_MAX: u16 = (1 << Self::PRERELEASE_BITS_OFFSET) - 1;
```

**File:** gossip/src/crds_gossip_pull.rs (L173-187)
```rust
    #[inline]
    fn lsb_mask(mask_bits: u32) -> u64 {
        // Mask with all least-significant (64 - mask_bits) bits set to 1.
        (!0u64).checked_shr(mask_bits).unwrap_or(0)
    }
    #[inline]
    pub(crate) fn canonical_mask(mask: u64, mask_bits: u32) -> u64 {
        // Normalize a mask so that all bits below mask_bits are 1s
        mask | Self::lsb_mask(mask_bits)
    }
    #[inline]
    pub(crate) fn hash_matches_mask_prefix(mask: u64, mask_bits: u32, hash_u64: u64) -> bool {
        let lsb_mask = Self::lsb_mask(mask_bits);
        (hash_u64 | lsb_mask) == Self::canonical_mask(mask, mask_bits)
    }
```

**File:** gossip/src/crds_gossip_pull.rs (L1409-1435)
```rust
    #[test]
    fn test_lsb_mask() {
        assert_eq!(CrdsFilter::lsb_mask(0), !0u64);
        assert_eq!(CrdsFilter::lsb_mask(1), !0u64 >> 1);
        assert_eq!(CrdsFilter::lsb_mask(4), !0u64 >> 4);
        assert_eq!(CrdsFilter::lsb_mask(64), 0);
        assert_eq!(CrdsFilter::lsb_mask(65), 0);
    }

    #[test]
    fn test_canonical_mask_normalizes_low_bits() {
        let mask_bits = 8;
        let lsb = CrdsFilter::lsb_mask(mask_bits);

        // Construct a mask with some garbage in the low bits
        let prefix: u64 = 0b1010_1100;
        let high = prefix << (64 - mask_bits);
        let garbage_low = 0x1234_5678_u64;
        let raw_mask = high | garbage_low;

        let canonical = CrdsFilter::canonical_mask(raw_mask, mask_bits);

        // High bits (prefix) are preserved
        assert_eq!(canonical >> (64 - mask_bits), prefix);
        // Low bits are all 1
        assert_eq!(canonical & lsb, lsb);
    }
```
