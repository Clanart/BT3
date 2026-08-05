[1](#0-0) [2](#0-1)

### Citations

**File:** accounts-db/src/accounts_index/stats.rs (L70-71)
```rust
    pub num_hashmap_reallocates: AtomicU64,
    pub hashmap_reallocate_us: AtomicU64,
```

**File:** accounts-db/src/accounts_index/stats.rs (L437-446)
```rust
                (
                    "num_hashmap_reallocates",
                    self.num_hashmap_reallocates.swap(0, Ordering::Relaxed),
                    i64
                ),
                (
                    "hashmap_reallocate_us",
                    self.hashmap_reallocate_us.swap(0, Ordering::Relaxed),
                    i64
                ),
```
