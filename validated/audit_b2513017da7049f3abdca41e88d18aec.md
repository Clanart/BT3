Audit Report

## Title
`CostModel::calculate_allocated_accounts_data_size` undercounts real account‑data growth, letting attackers bypass the per‑block accounts‑data‑size admission limit - ([File: cost-model/src/cost_model.rs])

## Summary
`CostModel::calculate_allocated_accounts_data_size` only inspects top-level transaction instructions and only recognizes account-data allocation when the outer `program_id` equals `system_program::id()`, invoking `SystemInstruction::CreateAccount`/`CreateAccountWithSeed`/`Allocate`/`AllocateWithSeed`/`CreateAccountAllowPrefund`. Any account-data growth performed via CPI into the System Program, or via direct in-place resize (`set_data_length`/`extend_from_slice`) by a program that owns the account, is invisible to this estimate, yet is fully realized on-chain and tracked separately by `Bank::update_accounts_data_size_delta_on_chain`. Because the cost tracker's block-level `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` admission check in `CostTracker::try_add` relies solely on the cost model's estimate, transactions using these common patterns contribute zero to the modeled budget while contributing their full real size to actual account-data growth.

## Finding Description
`CostModel::calculate_account_data_size_on_instruction` in `cost-model/src/cost_model.rs` (lines 242-261) gates its recognition of account-data allocation strictly on `program_id == &system_program::id()`, where `program_id` comes from `transaction.program_instructions_iter()` — i.e., only outer/top-level instructions. [1](#0-0) 

The enclosing function's doc comment explicitly acknowledges the narrow scope of the current approach ("at the moment, calculate account data size of account creation"). [2](#0-1) 

This estimate (`allocated_accounts_data_size`) is exactly what `CostTracker::try_add` uses to enforce the per-block accounts-data-growth admission limit, comparing against `self.limits.allocated_data_size` (derived from `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA = 100_000_000`). [3](#0-2) 

Meanwhile, the actual realized growth is tracked independently on-chain via `accounts_resize_delta`, summed in `Bank`'s post-execution accounting — a path entirely disjoint from the cost model's pre-execution estimate. [4](#0-3) 

Because a BPF program's CPI into `system_program::create_account`/`allocate` makes the *outer* `program_id` the calling program (not `system_program::id()`), and because direct resizing via `set_data_length`/`extend_from_slice` never touches the System Program at all, both mechanisms bypass `calculate_account_data_size_on_instruction`'s only recognized case, causing `calculate_allocated_accounts_data_size` to return `0` (or an undercount) for such transactions. The only real per-transaction guard on the resize path is `can_data_be_resized` inside `transaction-context/src/instruction_accounts.rs`, which bounds a single transaction's cumulative resize but is never summed into the leader's block-level `allocated_accounts_data_size` counter, so it provides no defense against the block-level admission bypass.

## Impact Explanation
This is an unprivileged, remotely reachable resource-accounting gap in the runtime/cost-model path. Ordinary transactions using common patterns — CPI-based account creation (e.g., SPL Token-2022 create-account flows) or direct account reallocation (e.g., Anchor `#[account(zero)]`/`realloc`) — are treated by the cost model as consuming zero accounts-data-size budget, while contributing their full real size to `Bank`'s on-chain accounts-data-size accounting. This allows the leader's `CostTracker` to admit far more real account-data growth per block than `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` intends, undermining the protocol's bound on state bloat — a non-RPC remote resource-exhaustion vector affecting AccountsDB growth, snapshot size, and replay cost across the validator set.

## Likelihood Explanation
High likelihood: CPI-based account creation and in-place account resizing are core, everyday patterns used by a large fraction of deployed Solana programs. An attacker requires no privileged access, no malicious validator collusion, and no novel program — only ordinary transactions invoking widely-deployed programs that use these idioms, submitted repeatedly to fill a block. This purely exploits a static-analysis gap in `CostModel::calculate_allocated_accounts_data_size`, which only recognizes top-level `system_program` instructions.

## Recommendation
Estimate `allocated_accounts_data_size` conservatively rather than optimistically: extend `calculate_account_data_size_on_instruction` (or its caller) to account for CPI'd System Program allocation instructions by inspecting inner instructions where available, and/or add a bounded worst-case contribution for any instruction whose program could resize/realloc accounts it owns (e.g., treat any writable, non-system-program account as a potential allocation up to some bound). At minimum, reconcile `Bank`'s realized `accounts_data_size_delta_on_chain` against the cost tracker's committed `allocated_accounts_data_size` per block, and apply backpressure/clamping to future block packing when real growth outpaces the modeled budget.

## Proof of Concept
1. Deploy or use an existing BPF program `P` that, when invoked, either (a) CPIs into `system_program::create_account`/`allocate` to create/grow an account it owns, or (b) directly calls `set_data_length`/`extend_from_slice` (`transaction-context/src/instruction_accounts.rs:194-226`) to grow an account's data up to the per-transaction maximum.
2. Submit a transaction whose only top-level instruction invokes `P` (top-level `program_id` is `P`, not `system_program::id()`).
3. Observe `CostModel::calculate_allocated_accounts_data_size` (`cost-model/src/cost_model.rs:263-301`) returns `0`/undercounted for this transaction, since `calculate_account_data_size_on_instruction` (lines 242-261) only special-cases top-level `system_program::id()` calls; consequently `CostTracker::try_add` (`cost-model/src/cost_tracker.rs:186-193`) never rejects it for `WouldExceedAccountDataBlockLimit`.
4. Repeat across many transactions/accounts to fill a block. After execution, `Bank`'s on-chain accounts-data-size delta accounting (via `accounts_resize_delta`, `transaction-context/src/instruction_accounts.rs:204`) will reflect real per-block growth far exceeding `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` (`cost-model/src/block_cost_limits.rs:35-37`), even though the cost tracker admitted every transaction as consuming zero/near-zero data-size budget.

### Citations

**File:** cost-model/src/cost_model.rs (L242-261)
```rust
    fn calculate_account_data_size_on_instruction(
        program_id: &Pubkey,
        instruction: SVMInstruction,
        feature_set: &FeatureSet,
    ) -> SystemProgramAccountAllocation {
        if program_id == &system_program::id() {
            if let Ok(instruction) =
                limited_deserialize(instruction.data, solana_packet::PACKET_DATA_SIZE as u64)
            {
                Self::calculate_account_data_size_on_deserialized_system_instruction(
                    instruction,
                    feature_set,
                )
            } else {
                SystemProgramAccountAllocation::Failed
            }
        } else {
            SystemProgramAccountAllocation::None
        }
    }
```

**File:** cost-model/src/cost_model.rs (L263-268)
```rust
    /// eventually, potentially determine account data size of all writable accounts
    /// at the moment, calculate account data size of account creation
    fn calculate_allocated_accounts_data_size<'a>(
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
        feature_set: &FeatureSet,
    ) -> u64 {
```

**File:** cost-model/src/cost_tracker.rs (L186-193)
```rust
        }

        let allocated_accounts_data_size =
            self.allocated_accounts_data_size + Saturating(tx_cost.allocated_accounts_data_size());

        if allocated_accounts_data_size.0 > self.limits.allocated_data_size {
            return Err(CostTrackerError::WouldExceedAccountDataBlockLimit);
        }
```

**File:** transaction-context/src/instruction_accounts.rs (L194-207)
```rust
    /// Resizes the account data (transaction wide)
    ///
    /// Fills it with zeros at the end if is extended or truncates at the end otherwise.
    pub fn set_data_length(&mut self, new_length: usize) -> Result<(), InstructionError> {
        self.can_data_be_resized(new_length)?;
        // don't touch the account if the length does not change
        if self.get_data().len() == new_length {
            return Ok(());
        }
        self.touch()?;
        self.update_accounts_resize_delta(new_length)?;
        self.account.resize(new_length, 0);
        Ok(())
    }
```
