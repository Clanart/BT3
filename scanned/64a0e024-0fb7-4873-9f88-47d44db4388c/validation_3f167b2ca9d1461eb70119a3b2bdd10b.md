[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** gossip/src/crds_gossip_pull.rs (L70-76)
```rust
// Loosest mask_bits floor accepted for incoming pull requests.
// `PACKET_DATA_SIZE` avoids rejecting honest smaller bloom filters.
static MIN_PULL_REQUEST_MASK_BITS: LazyLock<u32> = LazyLock::new(|| {
    let max_bits = (PACKET_DATA_SIZE * 8) as f64;
    let max_items = CrdsFilter::max_items(max_bits, FALSE_RATE, KEYS);
    CrdsFilter::mask_bits(MIN_NUM_BLOOM_ITEMS as f64, max_items)
});
```

**File:** gossip/src/crds_gossip_pull.rs (L96-103)
```rust
impl solana_sanitize::Sanitize for CrdsFilter {
    fn sanitize(&self) -> std::result::Result<(), solana_sanitize::SanitizeError> {
        if self.mask_bits < *MIN_PULL_REQUEST_MASK_BITS {
            return Err(solana_sanitize::SanitizeError::InvalidValue);
        }
        Ok(())
    }
}
```

**File:** gossip/src/crds_gossip_pull.rs (L513-527)
```rust
        let apply_filter = |request: &PullRequest| {
            if output_size_limit == 0 {
                return Vec::default();
            }
            let filter = &request.filter;
            let caller_wallclock = request.wallclock;
            if !caller_wallclock_window.contains(&caller_wallclock) {
                dropped_requests += 1;
                return Vec::default();
            }
            let scan_len = crds.filter_bitmask_scan_count(filter.mask, filter.mask_bits);
            // Charge only requests that passed cheaper pre-scan checks.
            if !try_consume_scan_budget(request, scan_len) {
                return Vec::default();
            }
```

**File:** gossip/src/crds_gossip_pull.rs (L786-801)
```rust
    fn test_crds_filter_sanitize_mask_bits_floor() {
        use solana_sanitize::{Sanitize, SanitizeError};

        assert_eq!(MIN_NUM_BLOOM_ITEMS, 65_536);
        assert_eq!(*MIN_PULL_REQUEST_MASK_BITS, 6);
        let filter = CrdsFilter {
            mask_bits: 5,
            ..CrdsFilter::default()
        };
        assert_eq!(filter.sanitize(), Err(SanitizeError::InvalidValue));
        let filter = CrdsFilter {
            mask_bits: 6,
            ..CrdsFilter::default()
        };
        assert_eq!(filter.sanitize(), Ok(()));
    }
```
