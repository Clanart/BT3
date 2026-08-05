[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/src/window_service.rs (L120-152)
```rust
    let (shred1, shred2) = match shred {
        PossibleDuplicateShred::LastIndexConflict(shred, conflict)
        | PossibleDuplicateShred::ErasureConflict(shred, conflict)
        | PossibleDuplicateShred::MerkleRootConflict(shred, conflict) => (shred, conflict),
        PossibleDuplicateShred::FixedFECChainedMerkleRootConflict(_slot) => {
            if no_verify_chained_merkle_root {
                // If we're in the full alpenglow epoch, we stop validating the chained merkle root.
                // In Alpenglow we only use the double merkle root
                return Ok(None);
            }
            blockstore.set_dead_slot(shred_slot)?;
            return Ok(None);
        }
        PossibleDuplicateShred::Exists(shred) => {
            // Unlike the other cases we have to wait until here to decide to handle the duplicate and store
            // in blockstore. This is because the duplicate could have been part of the same insert batch,
            // so we wait until the batch has been written.
            if blockstore.has_duplicate_shreds_in_slot(shred_slot) {
                return Ok(None); // A duplicate is already recorded
            }
            let Some(existing_shred_payload) = blockstore.is_shred_duplicate(&shred) else {
                return Ok(None); // Not a duplicate
            };
            blockstore.store_duplicate_slot(
                shred_slot,
                existing_shred_payload.clone(),
                shred.clone().into_payload(),
            )?;
            (shred, shred::Payload::from(existing_shred_payload))
        }
    };

    Ok(Some((shred1, shred2)))
```

**File:** core/src/window_service.rs (L167-201)
```rust
    let check_duplicate = |shred: PossibleDuplicateShred| -> Result<()> {
        if last_updated.elapsed().as_nanos() > root_bank.ns_per_slot {
            // Grabs bank forks lock once a slot
            last_updated = Instant::now();
            root_bank = bank_forks.read().unwrap().root_bank();
        }
        let shred_slot = shred.slot();
        let no_verify_chained_merkle_root = shred::filter::check_feature_activation_from_bank(
            &feature_set::alpenglow::id(),
            shred_slot,
            &root_bank,
        );

        let Some((shred1, shred2)) =
            check_duplicate_shred(blockstore, shred, no_verify_chained_merkle_root)?
        else {
            return Ok(());
        };

        if migration_status.should_respond_to_ancestor_hashes_requests(shred_slot) {
            // In alpenglow we store the duplicate block proofs in blockstore for the purposes of slashing,
            // however we do not need to propagate the duplicate proof through gossip.
            // We still propagate during the mixed migration epoch, to account for other nodes that are stuck
            // and require a duplicate proof to proceed
            cluster_info.push_duplicate_shred(&shred1, &shred2)?;
        }

        if !migration_status.is_alpenglow_enabled() {
            // The state machine can be exited as soon as alpenglow is enabled.
            // Notify duplicate consensus state machine. If channel is full we wait.
            duplicate_slots_sender.send(shred_slot)?;
        }

        Ok(())
    };
```

**File:** core/src/window_service.rs (L425-427)
```rust
                let handle_duplicate = |possible_duplicate_shred| {
                    let _ = check_duplicate_sender.send(possible_duplicate_shred);
                };
```
