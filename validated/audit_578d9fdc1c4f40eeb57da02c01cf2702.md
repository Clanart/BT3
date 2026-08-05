### Title
`distribute_epoch_rewards_in_partition` fails to add distributed block rewards to `capitalization`, corrupting the bank's total-supply invariant - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
This is a direct structural analog of the ConcentratedLiquidityPool.burn bug: a global "reserve" accounting variable (`Bank::capitalization`, Agave's on-chain analog of pool reserves — it must always equal the sum of all account lamport balances) is updated for only part of the lamports that are actually credited to real accounts. When stake accounts are rewarded during partitioned epoch-rewards distribution, both an inflation ("stake") reward and a separate "block" reward are added to the stake account's real lamport balance, but `capitalization` is only incremented for the inflation portion.

### Finding Description
In `build_updated_stake_reward`, both reward components are added directly to the stake account's actual lamports: [1](#0-0) 

That is, `account.checked_add_lamports(stake_reward)` and `account.checked_add_lamports(block_reward)` both mutate the real, on-disk account balance.

Back in the caller, `store_stake_accounts_in_partition` accumulates both quantities separately per successful reward: [2](#0-1) 

`distribute_epoch_rewards_in_partition` then updates `capitalization` using only the "minted" (inflation) side and only debits it on the burned side of the *block* reward — never crediting it for `block_reward_lamports_distributed`, the amount that was actually just added to real account balances: [3](#0-2) 

This is exactly the pattern in the reported bug: tokens/lamports are transferred out (here: minted into stake accounts) but the tracked aggregate reserve (`capitalization`) is only adjusted for a subset of the movement. Every other capitalization-adjusting call site in this codebase pairs balance changes 1:1 with capitalization changes — e.g. `store_account_and_update_capitalization` [4](#0-3) , `burn_and_purge_account` [5](#0-4) , `run_incinerator` [6](#0-5) , and the core-BPF migration's `update_captalization` [7](#0-6)  — none of these skip crediting capitalization for a value that is actually deposited into a live account. The epoch-rewards distribution path is the one place where a real lamport credit into an account (`block_reward`) has no matching capitalization update.

### Impact Explanation
If `block_reward` is genuinely newly emitted value (distinct from previously-capitalized transaction fees), skipping the capitalization credit means `capitalization` under-counts the real sum of all account balances after every rewards-distribution block featuring `block_revenue_sharing` [8](#0-7) . `capitalization` is a consensus-critical, hashed value (bank hash/accounts-lt-hash inputs, snapshot capitalization checks used by `ledger-tool` to detect divergence: [9](#0-8) ). A persistent, deterministic mismatch between tracked capitalization and the true sum of balances is exactly the same class of failure as the reported reserve bug: downstream capitalization-based sanity checks, snapshot validation, and any code that assumes `capitalization == Σ balances` (a network-wide invariant enforced across all validators) become permanently wrong, and would only be caught by manual `--enable-capitalization-change`-style intervention rather than being self-healing.

### Likelihood Explanation
This code runs unconditionally on every validator during epoch-rewards distribution partitions once `block_revenue_sharing` is active — it is not attacker-triggered, it fires deterministically for the normal reward-distribution flow, so if the accounting asymmetry is real it would manifest on every epoch boundary for every validator (100% of nodes, deterministically, so it would not cause consensus *divergence* per se, but it does corrupt the invariant network-wide simultaneously). I was not able to fully confirm, from the excerpts available, whether `block_reward` lamports are freshly minted at distribution time or were already credited to capitalization earlier (e.g., at reward-calculation time when the total pool is computed) — if the latter, the omission here is intentional (a transfer, not new emission) and this would not be a bug. This uncertainty should be resolved by inspecting `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` for wherever `block_reward` amounts are first computed and whether `capitalization.fetch_add` is called there for the full total (inflation + block) reward pool.

### Recommendation
Verify whether `block_reward_lamports_distributed` represents newly-minted value or already-capitalized fee revenue. If it is newly minted (not already reflected in `capitalization` from an earlier step), add a matching `self.capitalization.fetch_add(block_reward_lamports_distributed, Relaxed)` alongside the existing `stake_reward_lamports_minted` credit in `distribute_epoch_rewards_in_partition`, so that every lamport added to a real account via `checked_add_lamports` is paired with a capitalization update, consistent with every other capitalization-adjusting call site in the codebase.

### Proof of Concept
Not directly exploitable by an external actor (this is a bank-internal accounting path, not a user-triggerable path), so no PoC transaction can be constructed; the recommended validation is a unit test in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs` that computes `Σ stake_account.lamports()` before and after `distribute_epoch_rewards_in_partition` for a partition containing non-zero `block_reward` amounts (with `block_revenue_sharing` active), and asserts that `bank.capitalization()` after equals `bank.capitalization()` before plus the total real lamport delta across all touched accounts — this would immediately reveal the divergence if `block_reward_lamports_distributed` is indeed unbacked new value.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-198)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L394-398)
```rust
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
```

**File:** runtime/src/bank.rs (L3253-3261)
```rust
    fn burn_and_purge_account(&self, program_id: &Pubkey, mut account: AccountSharedData) {
        let old_data_size = account.data().len();
        self.capitalization.fetch_sub(account.lamports(), Relaxed);
        // Both resetting account balance to 0 and zeroing the account data
        // is needed to really purge from AccountsDb and flush the Stakes cache
        account.set_lamports(0);
        account.data_as_mut_slice().fill(0);
        self.store_account(program_id, &account);
        self.calculate_and_update_accounts_data_size_delta_off_chain(old_data_size, 0);
```

**File:** runtime/src/bank.rs (L4528-4534)
```rust
    fn run_incinerator(&self) {
        if let Some((account, _)) =
            self.get_account_modified_since_parent_with_fixed_root(&incinerator::id())
        {
            self.capitalization.fetch_sub(account.lamports(), Relaxed);
            self.store_account(&incinerator::id(), &AccountSharedData::default());
        }
```

**File:** runtime/src/bank.rs (L4829-4841)
```rust
            match new_account.lamports().cmp(&old_account.lamports()) {
                std::cmp::Ordering::Greater => {
                    let diff = new_account.lamports() - old_account.lamports();
                    trace!("store_account_and_update_capitalization: increased: {pubkey} {diff}");
                    self.capitalization.fetch_add(diff, Relaxed);
                }
                std::cmp::Ordering::Less => {
                    let diff = old_account.lamports() - new_account.lamports();
                    trace!("store_account_and_update_capitalization: decreased: {pubkey} {diff}");
                    self.capitalization.fetch_sub(diff, Relaxed);
                }
                std::cmp::Ordering::Equal => {}
            }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L480-498)
```rust
    fn update_captalization(
        &mut self,
        lamports_to_burn: u64,
        lamports_to_fund: u64,
    ) -> Result<(), CoreBpfMigrationError> {
        match lamports_to_burn.cmp(&lamports_to_fund) {
            Ordering::Greater => {
                self.capitalization
                    .fetch_sub(checked_sub(lamports_to_burn, lamports_to_fund)?, Relaxed);
            }
            Ordering::Less => {
                self.capitalization
                    .fetch_add(checked_sub(lamports_to_fund, lamports_to_burn)?, Relaxed);
            }
            Ordering::Equal => (),
        };

        Ok(())
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L120-126)
```rust
        self.update_sysvar_account(&sysvar::epoch_rewards::id(), |account| {
            if self.feature_set.snapshot().block_revenue_sharing {
                // Don't use `inherit_specially_retained_account_fields()` to
                // ensure that any remaining lamports get burned, lamports are
                // set to the rent-exempt minimum during `update_sysvar_account`,
                // and capitalization is updated
                create_account(
```

**File:** ledger-tool/src/main.rs (L2492-2505)
```rust
                        let amount = if pre_capitalization > post_capitalization {
                            format!("-{}", pre_capitalization - post_capitalization)
                        } else {
                            (post_capitalization - pre_capitalization).to_string()
                        };
                        let msg = format!("Capitalization change: {amount} lamports");
                        warn!("{msg}");
                        if !enable_capitalization_change {
                            eprintln!(
                                "{msg}\nBut `--enable-capitalization-change flag not provided"
                            );
                            exit(1);
                        }
                        Some(msg)
```
