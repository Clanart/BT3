[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/src/repair/repair_handler.rs (L161-182)
```rust
    fn run_parent_fec_set_count(
        &self,
        recycler: &PacketBatchRecycler,
        from_addr: &SocketAddr,
        slot: Slot,
        block_id: Hash,
        nonce: Nonce,
    ) -> Option<PacketBatch> {
        let (double_merkle_meta, slot_meta) = self
            .blockstore()
            .get_parent_repair_metadata(slot, block_id)
            .ok()??;

        let parent_slot = slot_meta.parent_slot?;
        let parent_block_id = slot_meta.parent_block_id;
        let parent_proof = double_merkle_meta.get_parent_info_proof()?.to_vec();

        let response = BlockIdRepairResponse::ParentFecSetCount {
            fec_set_count: double_merkle_meta.fec_set_count(),
            parent_info: (parent_slot, parent_block_id),
            parent_proof,
        };
```

**File:** core/src/repair/repair_handler.rs (L484-500)
```rust
        match response {
            BlockIdRepairResponse::ParentFecSetCount {
                fec_set_count,
                parent_info: (p_slot, p_block_id),
                parent_proof,
            } => {
                assert_eq!(
                    fec_set_count as usize,
                    fec_set_roots.len(),
                    "FEC set count should match"
                );
                assert_eq!(p_slot, parent_slot, "Parent slot should match");
                assert_eq!(
                    p_block_id,
                    Hash::default(),
                    "Parent block ID should be default"
                );
```

**File:** core/src/repair/block_id_repair_service.rs (L578-594)
```rust

        let Some(request) =
            // verify the response (and check merkle proof validity)
            state.outstanding_requests.register_response(
                nonce,
                &response,
                timestamp(),
                // If valid return the original request
                |block_id_request| *block_id_request,
            )
        else {
            debug!(
                "{my_pubkey}: Response with invalid nonce {nonce} or failed verification for {response:?}"
            );
            state.response_stats.invalid_packets += 1;
            return;
        };
```

**File:** core/src/repair/block_id_repair_service.rs (L605-632)
```rust
        match response {
            BlockIdRepairResponse::ParentFecSetCount {
                fec_set_count,
                parent_info: (p_slot, p_block_id),
                parent_proof: _,
            } => {
                // Queue a request to repair the parent (filtered out later if we already have the parent)
                state.push_pending_repair_event(RepairEvent::FetchBlock {
                    block: Block {
                        slot: p_slot,
                        block_id: p_block_id,
                    },
                });

                // Queue FecSetRoot requests
                state
                    .pending_repair_requests
                    .extend((0..fec_set_count).map(|i| {
                        let fec_set_index = i * DATA_SHREDS_PER_FEC_BLOCK as u32;
                        OutgoingMessage::Metadata(BlockIdRepairType::FecSetRoot {
                            slot,
                            block_id,
                            fec_set_index,
                        })
                    }));

                state.response_stats.parent_fec_set_count_responses += 1;
            }
```
