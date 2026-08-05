[1](#0-0) [2](#0-1)

### Citations

**File:** gossip/src/crds.rs (L22-26)
```rust
//! Merge strategy is implemented in:
//!     fn overrides(value: &CrdsValue, other: &VersionedCrdsValue) -> bool
//!
//! A value is updated to a new version if the labels match, and the value
//! wallclock is later, or the value hash is greater.
```

**File:** gossip/src/crds.rs (L193-214)
```rust
// Returns true if the first value updates the 2nd one.
// Both values should have the same key/label.
fn overrides(value: &CrdsValue, other: &VersionedCrdsValue) -> bool {
    assert_eq!(value.label(), other.value.label(), "labels mismatch!");
    // Contact-infos are special cased so that if there are
    // two running instances of the same node, the more recent start is
    // propagated through gossip regardless of wallclocks.
    if let CrdsData::ContactInfo(value) = value.data()
        && let CrdsData::ContactInfo(other) = other.value.data()
        && let Some(out) = value.overrides(other)
    {
        return out;
    }
    match value.wallclock().cmp(&other.value.wallclock()) {
        Ordering::Less => false,
        Ordering::Greater => true,
        // Ties should be broken in a deterministic way across the cluster.
        // For backward compatibility this is done by comparing hash of
        // serialized values.
        Ordering::Equal => other.value.hash() < value.hash(),
    }
}
```
