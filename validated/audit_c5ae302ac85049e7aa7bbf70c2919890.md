[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** connection-cache/src/connection_cache.rs (L153-168)
```rust
    fn create_connection(
        &self,
        lock_timing_ms: &mut u64,
        addr: &SocketAddr,
    ) -> CreateConnectionResult<<P as ConnectionPool>::BaseClientConnection> {
        let mut get_connection_map_lock_measure = Measure::start("get_connection_map_lock_measure");
        let mut map = self.map.write().unwrap();
        get_connection_map_lock_measure.stop();
        *lock_timing_ms = lock_timing_ms.saturating_add(get_connection_map_lock_measure.as_ms());
        // Read again, as it is possible that between read lock dropped and the write lock acquired
        // another thread could have setup the connection.

        let pool_status = map
            .get(addr)
            .map(|pool| pool.check_pool_status(self.connection_pool_size))
            .unwrap_or(PoolStatus::Empty);
```

**File:** connection-cache/src/connection_cache.rs (L199-209)
```rust
        let pool = map.get(addr).unwrap();
        let connection = pool.borrow_connection();

        CreateConnectionResult {
            connection,
            cache_hit,
            connection_cache_stats: self.stats.clone(),
            num_evictions,
            eviction_timing_ms,
        }
    }
```

**File:** connection-cache/src/connection_cache.rs (L238-261)
```rust
        let mut hit_cache = false;
        map.entry(*addr)
            .and_modify(|pool| {
                if matches!(
                    pool.check_pool_status(connection_pool_size),
                    PoolStatus::PartiallyFull
                ) {
                    let idx = pool.add_connection(config, addr);
                    if let Some(sender) = async_connection_sender {
                        debug!(
                            "Sending async connection creation {} for {addr}",
                            pool.num_connections() - 1
                        );
                        sender.send((idx, *addr)).unwrap();
                    };
                } else {
                    hit_cache = true;
                }
            })
            .or_insert_with(|| {
                let mut pool = connection_manager.new_connection_pool();
                pool.add_connection(config, addr);
                pool
            });
```
