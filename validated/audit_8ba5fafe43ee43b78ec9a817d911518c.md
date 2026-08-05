[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/src/repair/repair_response.rs (L18-20)
```rust
    let shred = blockstore
        .get_data_shred(slot, shred_index)
        .expect("Blockstore could not get data shred");
```

**File:** core/src/repair/repair_response.rs (L21-23)
```rust
    shred
        .map(|shred| repair_response_packet_from_bytes(shred, dest, nonce))
        .unwrap_or(None)
```

**File:** core/src/repair/standard_repair_handler.rs (L24-38)
```rust
    fn repair_response_packet(
        &self,
        slot: Slot,
        shred_index: u64,
        dest: &SocketAddr,
        nonce: Nonce,
    ) -> Option<Packet> {
        repair_response::repair_response_packet(
            self.blockstore.as_ref(),
            slot,
            shred_index,
            dest,
            nonce,
        )
    }
```

**File:** core/src/repair/repair_handler.rs (L63-130)
```rust
    fn run_window_request(
        &self,
        recycler: &PacketBatchRecycler,
        from_addr: &SocketAddr,
        slot: Slot,
        shred_index: u64,
        nonce: Nonce,
    ) -> Option<PacketBatch> {
        // Try to find the requested index in one of the slots
        let packet = self.repair_response_packet(slot, shred_index, from_addr, nonce)?;
        Some(
            RecycledPacketBatch::new_with_recycler_data(
                recycler,
                "run_window_request",
                vec![packet],
            )
            .into(),
        )
    }

    fn run_window_request_for_block_id(
        &self,
        recycler: &PacketBatchRecycler,
        from_addr: &SocketAddr,
        slot: Slot,
        shred_index: u64,
        block_id: Hash,
        nonce: Nonce,
    ) -> Option<PacketBatch> {
        let shred = self
            .blockstore()
            .get_data_shred_for_block_id(slot, shred_index, block_id)
            .ok()??;
        let packet = repair_response_packet_from_bytes(shred, from_addr, nonce)?;
        Some(
            RecycledPacketBatch::new_with_recycler_data(
                recycler,
                "run_window_request_for_block_id",
                vec![packet],
            )
            .into(),
        )
    }

    fn run_highest_window_request(
        &self,
        recycler: &PacketBatchRecycler,
        from_addr: &SocketAddr,
        slot: Slot,
        highest_index: u64,
        nonce: Nonce,
    ) -> Option<PacketBatch> {
        // Try to find the requested index in one of the slots
        let meta = self.blockstore().meta(slot).ok()??;
        if meta.received > highest_index {
            // meta.received must be at least 1 by this point
            let packet = self.repair_response_packet(slot, meta.received - 1, from_addr, nonce)?;
            return Some(
                RecycledPacketBatch::new_with_recycler_data(
                    recycler,
                    "run_highest_window_request",
                    vec![packet],
                )
                .into(),
            );
        }
        None
    }
```
