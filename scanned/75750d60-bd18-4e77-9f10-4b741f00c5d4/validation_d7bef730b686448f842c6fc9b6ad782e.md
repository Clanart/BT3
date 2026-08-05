[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/accounts_index/accounts_index_storage.rs (L58-64)
```rust
        let is_disk_index_enabled = storage.is_disk_index_enabled();
        let num_threads = if is_disk_index_enabled {
            threads.get()
        } else {
            // no disk index, so only need 1 thread to report stats
            1
        };
```

**File:** accounts-db/src/accounts_index/accounts_index_storage.rs (L79-88)
```rust
                    Builder::new()
                        .name(format!("solIdxFlusher{idx:02}"))
                        .spawn(move || {
                            storage_.background(
                                vec![local_exit, system_exit],
                                in_mem_,
                                can_advance_age,
                            );
                        })
                        .unwrap()
```
