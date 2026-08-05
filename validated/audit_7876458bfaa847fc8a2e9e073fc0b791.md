[1](#0-0) [2](#0-1)

### Citations

**File:** perf/src/data_budget.rs (L40-104)
```rust
    pub fn take(&self, size: usize) -> bool {
        let mut bytes = self.bytes.load(Ordering::Acquire);
        loop {
            bytes = match self.bytes.compare_exchange_weak(
                bytes,
                match bytes.checked_sub(size) {
                    None => return false,
                    Some(bytes) => bytes,
                },
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return true,
                Err(bytes) => bytes,
            }
        }
    }

    // Updates timestamp and returns true, if at least given milliseconds
    // has passed since last update. Otherwise returns false.
    fn can_update(&self, duration_millis: u64) -> bool {
        let now = solana_time_utils::timestamp();
        let mut asof = self.asof.load(Ordering::Acquire);
        while asof.saturating_add(duration_millis) <= now {
            asof = match self.asof.compare_exchange_weak(
                asof,
                now,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return true,
                Err(asof) => asof,
            }
        }
        false
    }

    /// Updates the budget if at least given milliseconds has passed since last
    /// update. Updater function maps current value of bytes to the new one.
    /// Returns current data-budget after the update.
    pub fn update<F>(&self, duration_millis: u64, updater: F) -> usize
    where
        F: Fn(usize) -> usize,
    {
        if self.can_update(duration_millis) {
            let mut bytes = self.bytes.load(Ordering::Acquire);
            loop {
                bytes = match self.bytes.compare_exchange_weak(
                    bytes,
                    updater(bytes),
                    Ordering::AcqRel,
                    Ordering::Acquire,
                ) {
                    Ok(_) => break,
                    Err(bytes) => bytes,
                }
            }
        }
        self.bytes.load(Ordering::Acquire)
    }

    #[must_use]
    pub fn check(&self, size: usize) -> bool {
        size <= self.bytes.load(Ordering::Acquire)
    }
```
