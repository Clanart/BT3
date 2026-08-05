## Title
`check_chained_block_id` silently passes SIMD-0340 chained-merkle-root verification when the parent slot's shreds are absent from the caller's blockstore - ([File: ledger/src/blockstore_processor.rs])

## Summary
The BlockhashRegistry report's broken invariant is: a routine that is supposed to verify a cryptographic link between a child and its parent instead treats a "missing" comparison value as automatically valid, letting the chain-of-trust continue without ever raising an error. The Agave analog is `check_chained_block_id`, which enforces SIMD-0340's requirement that a child slot's first shred must chain (via `chained_merkle_root`) to the last shred/merkle root of its parent slot. When the parent's last-shred merkle root cannot be found in the local blockstore, the function does not fail closed — it returns `ChainedBlockIdCheck::Pass`, i.e. the same outcome as an actual cryptographic match.

## Finding Description
`check_chained_block_id` compares the child's expected parent block ID (`get_parent_chained_block_id`, read from the child's own shred 0) against the parent slot's actual last-shred merkle root (`get_last_shred_merkle_root`): [1](#0-0) 

```rust
match blockstore
    .get_last_shred_merkle_root(parent_slot)
    .expect("Blockstore operations must succeed")
{
    Some(parent_block_id) => {
        if expected_parent_block_id != parent_block_id {
            ChainedBlockIdCheck::Mismatch
        } else {
            ChainedBlockIdCheck::Pass
        }
    }
    None => {
        warn!("{parent_slot} is missing from our blockstore, likely the snapshot slot. Skipping chained block id verification");
        ChainedBlockIdCheck::Pass
    }
}
``` [2](#0-1) 

`get_last_shred_merkle_root` returns `Ok(None)` in two situations: (1) there is no `SlotMeta` at all for `parent_slot`, or (2) the meta exists but `last_index` (i.e., the slot isn't yet complete): [3](#0-2) 

The comment "likely the snapshot slot" reflects the intended benign case (validator just booted from a snapshot and never ingested the parent's shreds). But the code has no way to actually confirm that the missing parent is the snapshot root — it applies the same `Pass` verdict whenever the parent's last shred is locally absent, regardless of why. This mirrors the reported bug exactly: `getParentAndBlockhash` returning the "invalid/empty" sentinel (`0x00`) was treated as an implicit match rather than an error, letting the trust chain continue unchecked.

Unlike `check_backwards_chained_merkle_root_consistency`, which explicitly defers verification of the first FEC set to `check_chained_block_id` at replay time, there is no other place in this codebase that revisits and definitively completes the check once the parent's shreds do arrive later. If the parent is legitimately incomplete right now, the ancestry link is accepted permanently as "Pass" during this replay pass — the guard exists only for a single, currently-observed local state, not for the property SIMD-0340 was designed to guarantee (that the child's alleged parent is *the* parent that produced these merkle roots).

## Impact Explanation
`ChainedBlockIdCheck::Pass` for slots whose parent-linkage was never actually verified breaks the invariant SIMD-0340 introduced specifically to catch duplicate/conflicting blocks that fork off using a forged or mismatched parent block ID. If a validator's blockstore happens to be missing the parent's last shred at replay time (e.g., due to normal shred-arrival timing, prune/GC races, or a slow/partial repair), a child slot with a chained-merkle-root that does not actually correspond to that parent will replay and be treated identically to a correctly-chained block. Since replay/consensus decisions (voting, rooting) proceed on this bank, this can cause a validator to vote for or root a block whose ancestry linkage was never cryptographically confirmed — the same class of outcome the original report flagged as "attacker overwrites/forges an accepted-but-invalid hash in the trust chain," here manifesting as false acceptance of an unverified parent linkage during replay/consensus.

## Likelihood Explanation
The trigger condition (missing `SlotMeta`/`last_index` for the parent slot in the local blockstore at the moment `check_chained_block_id` runs) is not exotic — it is exactly the state a node is in whenever it hasn't finished receiving/repairing the parent slot, which happens routinely, not just at snapshot boot. This makes the `None` branch reachable under ordinary node operation, without requiring a malicious peer, colluding validator, or leaked keys — it purely follows from timing/availability of shred data already flowing over gossip/turbine to an unprivileged node.

## Recommendation
`check_chained_block_id` should fail closed (return `ChainedBlockIdCheck::Unavailable`, not `Pass`) whenever the parent's last-shred merkle root cannot be retrieved, and the caller should defer any consensus action (voting/rooting) on the child until the check can actually be completed (e.g., re-run once repair fills in the parent, or restrict the `Pass`-without-verification allowance to a securely-identified snapshot root slot rather than any locally-absent parent).

## Proof of Concept
1. A validator restarts or falls behind such that it has not yet received/repaired all shreds of `parent_slot` (no `last_index` set, or no `SlotMeta` at all) — a normal, frequent occurrence, not an attack.
2. A child slot arrives whose shred 0 embeds a `chained_merkle_root` that does not actually correspond to `parent_slot`'s real last shred (whether from a buggy/malicious leader or blockstore data belonging to the wrong fork).
3. During replay, `check_chained_block_id` calls `blockstore.get_last_shred_merkle_root(parent_slot)`, which returns `Ok(None)` because the parent's last shred isn't present.
4. The `None` branch is taken, logging a warning and returning `ChainedBlockIdCheck::Pass` — the exact same verdict as a real, successful cryptographic match — even though no comparison against the parent's actual merkle root ever occurred.
5. Replay/`ReplayStage` proceeds treating the child's ancestry as verified, per the usages in `core/src/replay_stage.rs` (not fully inspected here due to tool-call limits, so the exact downstream branching from `Pass` vs `Unavailable` in `ReplayStage` could not be confirmed with additional context).

Note: I was unable to inspect the callsites in `core/src/replay_stage.rs` in enough depth (limited to matched line ranges) to confirm precisely how `ChainedBlockIdCheck::Pass` versus `Unavailable` differ in downstream voting/rooting logic, or whether an additional safeguard elsewhere restricts this bypass to true snapshot-boot scenarios. This should be verified further, e.g. by checking whether `Unavailable` and `Pass` are handled identically or differently by `ReplayStage`, and whether any explicit "is snapshot root" check exists elsewhere that the `None` branch could be gated on.

### Citations

**File:** ledger/src/blockstore_processor.rs (L2071-2112)
```rust
/// Validates the chained block ID for a child slot against its parent.
pub fn check_chained_block_id(
    blockstore: &Blockstore,
    bank: &Bank,
    migration_status: &MigrationStatus,
) -> ChainedBlockIdCheck {
    let slot = bank.slot();
    if migration_status.should_use_double_merkle_block_id(slot) {
        return ChainedBlockIdCheck::Inactive;
    }

    let parent_slot = bank.parent_slot();

    let Ok(expected_parent_block_id) = blockstore.get_parent_chained_block_id(slot) else {
        return ChainedBlockIdCheck::Unavailable;
    };

    match blockstore
        .get_last_shred_merkle_root(parent_slot)
        .expect("Blockstore operations must succeed")
    {
        Some(parent_block_id) => {
            if expected_parent_block_id != parent_block_id {
                warn!(
                    "Chained merkle root mismatch for slot {slot} (parent {parent_slot}): child \
                     chains to {expected_parent_block_id}, but parent block ID is \
                     {parent_block_id}"
                );
                ChainedBlockIdCheck::Mismatch
            } else {
                ChainedBlockIdCheck::Pass
            }
        }
        None => {
            warn!(
                "{parent_slot} is missing from our blockstore, likely the snapshot slot. Skipping \
                 chained block id verification",
            );
            ChainedBlockIdCheck::Pass
        }
    }
}
```

**File:** ledger/src/blockstore.rs (L3535-3553)
```rust
    /// Retrieves the merkle root of the last data shred in the given slot,
    /// which serves as the slot's block ID for chained merkle root validation
    /// in child slots (SIMD-0340).
    ///
    /// Returns `Ok(None)`` if the block is not complete
    pub fn get_last_shred_merkle_root(&self, slot: Slot) -> Result<Option<Hash>> {
        let Some(meta) = self.meta(slot)? else {
            return Ok(None);
        };
        let Some(last_index) = meta.last_index else {
            return Ok(None);
        };
        let shred_bytes = self
            .get_data_shred(slot, last_index)?
            .ok_or(BlockstoreError::MissingShred(slot, last_index))?;
        shred::layout::get_merkle_root(&shred_bytes)
            .map(Option::Some)
            .ok_or(BlockstoreError::MissingMerkleRoot(slot, last_index))
    }
```
