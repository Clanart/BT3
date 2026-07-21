Looking at the codebase, I found a direct analog to the Pool bug. The external bug is about a contract designed to be upgradeable but permanently broken because an initializer was never called, leaving `owner = address(0)`. The analog here is `PartialBlockHash` designed to include the parent block's commitment but permanently using `Felt::ZERO` instead — a TODO comment explicitly acknowledges the missing initialization.

### Title
`PartialBlockHash` Hardcodes Zero for Parent Hash, Making `ProposalCommitment` Fail to Chain Blocks — (`crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

### Summary
`PartialBlockHash::from_partial_block_hash_components` — the function that produces the `ProposalCommitment` that every consensus node signs over — calls `calculate_block_hash` with two hardcoded zero constants: `GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH = GlobalRoot(Felt::ZERO)` and `PARENT_HASH_FOR_PARTIAL_BLOCK_HASH = BlockHash(Felt::ZERO)`. A developer TODO explicitly acknowledges the parent hash should be the actual parent's partial block hash, not zero. Because the parent hash is always zero, the `ProposalCommitment` never binds to the parent block, breaking the chain-binding invariant of the consensus commitment.

### Finding Description

In `crates/starknet_api/src/block_hash/block_hash_calculator.rs`:

```rust
impl PartialBlockHash {
    // TODO(Ariel): Use parent_partial_block_hash instead of zero.
    const GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH: GlobalRoot = GlobalRoot(Felt::ZERO);
    const PARENT_HASH_FOR_PARTIAL_BLOCK_HASH: BlockHash = BlockHash(Felt::ZERO);

    pub fn from_partial_block_hash_components(
        partial_block_hash_components: &PartialBlockHashComponents,
    ) -> StarknetApiResult<Self> {
        let block_hash = calculate_block_hash(
            partial_block_hash_components,
            Self::GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH,   // always Felt::ZERO
            Self::PARENT_HASH_FOR_PARTIAL_BLOCK_HASH,   // always Felt::ZERO
        )?;
        Ok(Self(block_hash.0))
    }
}
``` [1](#0-0) 

This function is called in `BlockExecutionArtifacts::commitment()` to produce the `ProposalCommitment`:

```rust
pub fn commitment(&self) -> ProposalCommitment {
    ProposalCommitment {
        partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
            &self.partial_block_hash_components,
        )
        .expect("Unable to calculate the proposal commitment"),
    }
}
``` [2](#0-1) 

The `ProposalCommitment` is what every consensus validator independently recomputes and checks against the proposer's `ProposalFin`:

```rust
// TODO(matan): Switch to signature validation.
if built_block != received_fin.proposal_commitment {
    CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
    return Err(ValidateProposalError::ProposalFinMismatch);
}
``` [3](#0-2) 

The `calculate_block_hash` function chains 14 fields into a Poseidon hash. The parent hash occupies the final slot:

```rust
.chain(&Felt::ZERO)          // reserved zero field
.chain(&previous_block_hash.0)  // parent hash — always Felt::ZERO in PartialBlockHash
.get_poseidon_hash(),
``` [4](#0-3) 

Because `PARENT_HASH_FOR_PARTIAL_BLOCK_HASH` is never initialized to the actual parent's partial block hash (the TODO is unimplemented), the `ProposalCommitment` for every block at every height is computed as if the parent hash is zero. Two blocks at the same height with identical transaction/state-diff content but different parents produce an identical `ProposalCommitment`.

The actual `BlockHash` stored in storage is computed separately in `finalize_commitment_output` using the real `global_root` and `previous_block_hash` — but this happens *after* consensus has already agreed on the `ProposalCommitment`:

```rust
Some(calculate_block_hash(
    &partial_block_hash_components,
    global_root,
    previous_block_hash,
)?)
``` [5](#0-4) 

The consensus layer and the storage layer therefore use different hash inputs: consensus uses `(global_root=0, parent_hash=0)`, while storage uses the real values. The commitment that validators sign over does not commit to the parent block.

### Impact Explanation

**Impact: High** — The `ProposalCommitment` is the authoritative value that all consensus nodes sign over and that determines block acceptance. Because the parent hash is always zero in this commitment, the consensus agreement does not cryptographically chain block N to block N-1. In a scenario where two competing blocks exist at height N-1 (e.g., after a reorg or during a fork), a proposer can build block N on either parent and produce the same `ProposalCommitment`. Validators independently recompute the same zero-parent-hash commitment and accept the block. The final `BlockHash` written to storage would then chain from the wrong parent, producing a wrong authoritative block hash value. This matches: *"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value"* — the stored `BlockHash` and any RPC response derived from it would be wrong.

### Likelihood Explanation

The trigger is any proposer in any consensus round — no privilege is required. The condition is that a reorg or competing parent exists at height N-1, which is a normal operational scenario in a live network. The TODO comment confirms the developers are aware the parent hash is missing but have not yet implemented the fix.

### Recommendation

Implement the acknowledged TODO: replace `PARENT_HASH_FOR_PARTIAL_BLOCK_HASH: BlockHash = BlockHash(Felt::ZERO)` with the actual parent block's `PartialBlockHash`. The batcher already computes and caches the parent's `PartialBlockHashComponents` in `get_parent_proposal_commitment`:

```rust
PartialBlockHash::from_partial_block_hash_components(&components)
``` [6](#0-5) 

Pass this value into `PartialBlockHash::from_partial_block_hash_components` as the `previous_block_hash` argument instead of `Felt::ZERO`, so the `ProposalCommitment` cryptographically chains to the parent block.

### Proof of Concept

1. At height N-1, two competing blocks B1 and B2 exist with the same state diff but different block hashes H1 and H2 (e.g., different timestamps).
2. A proposer builds block N on top of B2 (hash H2), executing the same transactions as would be executed on top of B1.
3. The proposer computes `ProposalCommitment_N = PartialBlockHash(components_N, global_root=0, parent_hash=0)`.
4. Validators independently execute block N on top of B1 (the canonical parent) and compute `ProposalCommitment_N' = PartialBlockHash(components_N, global_root=0, parent_hash=0)`.
5. Since both use `parent_hash=0`, `ProposalCommitment_N == ProposalCommitment_N'`. Validators accept the block.
6. `finalize_commitment_output` computes `BlockHash_N = calculate_block_hash(components_N, global_root, H2)` — chaining from the wrong parent H2.
7. The stored `BlockHash_N` is wrong; any RPC call returning it returns an authoritative-looking wrong value.

The direct analog to the Pool bug: just as `__Ownable_init()` was never called so `owner` stayed `address(0)` making `_authorizeUpgrade` permanently broken, here `PARENT_HASH_FOR_PARTIAL_BLOCK_HASH` is never initialized to the actual parent hash so it stays `Felt::ZERO`, making the `ProposalCommitment` permanently fail to bind to the parent block.

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L189-206)
```rust
impl PartialBlockHash {
    // TODO(Ariel): Use parent_partial_block_hash instead of zero.
    const GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH: GlobalRoot = GlobalRoot(Felt::ZERO);
    const PARENT_HASH_FOR_PARTIAL_BLOCK_HASH: BlockHash = BlockHash(Felt::ZERO);

    /// Hash of [`PartialBlockHashComponents`].
    /// Uses the same formula as [`calculate_block_hash`] with the fixed constants above for the
    /// state root and parent hash.
    pub fn from_partial_block_hash_components(
        partial_block_hash_components: &PartialBlockHashComponents,
    ) -> StarknetApiResult<Self> {
        let block_hash = calculate_block_hash(
            partial_block_hash_components,
            Self::GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH,
            Self::PARENT_HASH_FOR_PARTIAL_BLOCK_HASH,
        )?;
        Ok(Self(block_hash.0))
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L278-280)
```rust
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
```

**File:** crates/apollo_batcher/src/block_builder.rs (L215-222)
```rust
    pub fn commitment(&self) -> ProposalCommitment {
        ProposalCommitment {
            partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
                &self.partial_block_hash_components,
            )
            .expect("Unable to calculate the proposal commitment"),
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L243-247)
```rust
    // TODO(matan): Switch to signature validation.
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L561-565)
```rust
                Some(calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?)
```

**File:** crates/apollo_batcher/src/batcher.rs (L1422-1430)
```rust
                Ok(Some(ProposalCommitment {
                    partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
                        &components,
                    )
                    .map_err(|e| {
                        error!("Failed to compute partial block hash: {}", e);
                        BatcherError::InternalError
                    })?,
                }))
```
