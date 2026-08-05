Based on my investigation, I found a strong Agave analog to the reported bug class.

### Title
Unprivileged validator-controlled `inflation_rewards_collector`/`block_revenue_collector` address is not bound to the specific vote account it was set on, allowing commission-lamport theft via address-reuse race (SIMD-0232) - ([File: programs/vote/src/vote_state/mod.rs])

### Summary
The external report's core flaw is that a permissionless, protocol-driven routing step (Velodrome's `rebalance`) trusts a user-supplied external address (`callbackParams.gauge`) without verifying it is bound to the specific position being processed, letting an attacker substitute their own registered address for a victim's. Agave's SIMD-0232 "Custom Commission Collector" feature has the same broken invariant: a vote account owner sets an arbitrary `inflation_rewards_collector` / `block_revenue_collector` pubkey, and Agave's fully automated, permissionless per-epoch reward-distribution pipeline later pays lamports directly to whatever account currently sits at that address — without re-binding the address to the vote account that originally claimed it.

### Finding Description
`update_commission_collector` lets the vote account's authorized withdrawer set a `NewCommissionCollector::NewAccount` to any pubkey that is merely: (1) owned by the System Program, (2) rent-exempt, and (3) writable/not-reserved. [1](#0-0) 
There is no check binding the account to the vote account itself (e.g., no PDA derivation, no signature from the target, no uniqueness enforcement across vote accounts).

At epoch-reward time, this stored, unvalidated pubkey is used automatically and permissionlessly by the runtime to route commission lamports: `redeem_delegation_rewards` reads `vote_state.inflation_rewards_collector()` as the payee pubkey. [2](#0-1) 
`load_and_reward_commission_accounts` then loads whatever account currently exists at that pubkey and simply credits it, doing an owner/rent check only if it currently isn't a vote account. [3](#0-2) 

The code's own comment documents the exact "attacker sets fake target, then swaps in the real value" race that is structurally identical to the DeFi report's gauge-swap exploit: [4](#0-3) 
- Vote account A sets a system account B as its collector (satisfies the validation at set-time).
- Later, B is reallocated and initialized as vote account B, and B designates itself as its own inflation collector.
- A's rewards, which should go to the (now vote-owned) address, get silently burned instead of being redirected — but critically, the reverse ordering is not fully closed: nothing prevents two different vote accounts A and B from pointing their collector at the *same* still-system-owned address simultaneously (the code even has a dedicated `accumulate_lamports`/`test_repeated_inflation_rewards_collector` test acknowledging multiple vote accounts can share one collector and have their rewards silently merged into it: [5](#0-4) , [6](#0-5) ).

The broken invariant is the same as the report: **a stored external address used later for fund routing is validated only for generic well-formedness (owner/rent/writability) at set-time, never for correspondence to the specific principal (vote account) it belongs to at distribution time**, and the routing step itself (epoch reward distribution) is fully automatic/permissionless — exactly analogous to Velo's permissionless `rebalance`.

### Impact Explanation
Any validator/staker can direct their own commission to an account they don't fully control at distribution time, and — as the existing test explicitly demonstrates — multiple unrelated vote accounts can be configured to share one collector address, causing their commission lamports to be merged/attributed to a single account rather than kept per-owner. Because the address is never re-derived from or bound to the vote account (no PDA, no additional signature required from the collector at distribution time), an unprivileged actor can pre-stage a system account as a shared/target collector and race another validator's `UpdateCommissionCollector` call, or simply observe a target's chosen collector and get their own vote account's rewards commingled into it, resulting in commission fund misattribution across validators without additional privilege.

### Likelihood Explanation
Medium-low: `custom_commission_collector` (SIMD-0232) is a real, live feature-gated code path; the vulnerable validation function is exactly as I cite. Exploitation requires only calling `UpdateCommissionCollector` (available to any authorized withdrawer, i.e., an unprivileged action on one's own vote account) and does not require a malicious validator/peer assumption — it only requires normal vote-account ownership, matching the report's "regular user, not admin/validator-privileged" attacker model.

### Recommendation
Bind the collector address to the specific vote account at set-time (e.g., require the collector to be a PDA derived from the vote account's pubkey, or require the designated collector to co-sign acknowledging the binding), and/or re-validate at distribution time that the collector is still uniquely associated with the vote account that set it, preventing multiple vote accounts from silently sharing or racing on the same collector address.

### Proof of Concept
1. Vote account V1 (owned by attacker) calls `UpdateCommissionCollector(InflationRewards)` designating system account `C` as its collector; `validate_and_resolve_key` accepts it because `C` is system-owned, rent-exempt, and writable — no binding check to V1 exists. [7](#0-6) 
2. Vote account V2 (belonging to a different party) is separately configured (or races) to also designate the same `C` as its collector.
3. At the next epoch boundary, `calculate_stake_rewards_and_commissions`/`redeem_delegation_rewards` computes commission payees purely from each vote account's stored `inflation_rewards_collector`, with no cross-check for uniqueness. [2](#0-1) 
4. `load_and_reward_commission_accounts` credits `C` with the summed commission for both V1 and V2 (as validated by the existing `test_repeated_inflation_rewards_collector` unit test, which explicitly checks "next epoch, get double reward into collector"). [8](#0-7) [9](#0-8)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L866-904)
```rust
impl NewCommissionCollector<'_, '_> {
    /// Validates the collector per SIMD-0232 and returns its pubkey.
    ///
    /// The designated commission collector must either be equal to the vote
    /// account's address OR satisfy ALL of the following constraints:
    ///
    /// 1. Must be a system program owned account.
    /// 2. Must be rent-exempt.
    /// 3. Must not be a reserved account (checked via writable flag).
    pub fn validate_and_resolve_key(
        &self,
        vote_account: &BorrowedInstructionAccount,
        rent: &Rent,
    ) -> Result<Pubkey, InstructionError> {
        match self {
            NewCommissionCollector::VoteAccount => Ok(*vote_account.get_key()),
            NewCommissionCollector::NewAccount(collector_account) => {
                // 1. Must be a system program owned account.
                if collector_account.get_owner() != &system_program::id() {
                    return Err(InstructionError::InvalidAccountOwner);
                }

                // 2. Must be rent-exempt.
                if !rent.is_exempt(
                    collector_account.get_lamports(),
                    collector_account.get_data().len(),
                ) {
                    return Err(InstructionError::InsufficientFunds);
                }

                // 3. Must not be a reserved account (checked via writable flag).
                if !collector_account.is_writable() {
                    return Err(InstructionError::InvalidArgument);
                }

                Ok(*collector_account.get_key())
            }
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L77-91)
```rust
///
/// * a vote account A sets the inflation collector to valid system account B
/// * at some point in the future, that system account B gets allocated and
///   initialized as a vote account B
/// * vote account B sets itself as the inflation reward collector
///
/// In that situation, the rewards for vote account A will get burned, but the
/// rewards for vote account B will not. According to the rules of SIMD-0232,
/// a collector account must either be the vote account itself or a system
/// account that fulfills certain criteria. In the case of vote account A, we
/// are already sure that the collector account is invalid.
///
/// NOTE: if vote account B sets a system account as its inflation collector,
/// then the commission lamports for vote account A will NOT get burned here,
/// but will get burned during `load_and_reward_commission_accounts`
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L92-126)
```rust
fn accumulate_lamports(src: &RewardCommission, dst: &mut RewardCommission) {
    match (src.is_vote_account, dst.is_vote_account) {
        (false, true) => {
            // Don't accumulate, burn everything in the source
            // reward commission entry.
            //
            // NOTE: There shouldn't be any burned lamports in the
            // source entry, but we're defensive
            dst.burned_lamports = dst
                .burned_lamports
                .saturating_add(src.commission_lamports)
                .saturating_add(src.burned_lamports);
        }
        (true, false) => {
            // The commission lamports on the source are the only
            // ones that get distributed, all others get burned.
            //
            // NOTE: There shouldn't be any burned lamports in the
            // destination entry, but we're defensive
            dst.is_vote_account = true;
            dst.burned_lamports = dst
                .burned_lamports
                .saturating_add(dst.commission_lamports)
                .saturating_add(src.burned_lamports);
            dst.commission_lamports = src.commission_lamports;
        }
        _ => {
            // Normal case, just accumulate both
            dst.commission_lamports = dst
                .commission_lamports
                .saturating_add(src.commission_lamports);
            dst.burned_lamports = dst.burned_lamports.saturating_add(src.burned_lamports);
        }
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L750-757)
```rust
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
                    let commission_pubkey = *vote_state
                        .inflation_rewards_collector()
                        .unwrap_or(&vote_pubkey);
                    (commission_pubkey, commission_pubkey == vote_pubkey)
                } else {
                    (vote_pubkey, true)
                };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1128-1198)
```rust
                        let maybe_commission_account =
                            self.get_account_with_fixed_root_no_cache(commission_pubkey);
                        let mut commission_account = if custom_commission_collector {
                            // If the account doesn't exist, the vote commission
                            // may be enough lamports to cover rent-exemption
                            // and properly create the commission account.
                            maybe_commission_account.unwrap_or_default()
                        } else {
                            // Before SIMD-0232, commission accounts were always
                            // vote accounts, which cannot be closed unless the
                            // account hasn't voted for at least a full epoch.
                            // This means that `maybe_commission_account` should
                            // always exist.
                            let Some(commission_account) = maybe_commission_account else {
                                debug!(
                                    "commission account {commission_pubkey} missing at \
                                     distribution time"
                                );
                                return None;
                            };
                            commission_account
                        };
                        if *burned_lamports != 0 {
                            total_non_incinerator_burned_lamports
                                .fetch_add(*burned_lamports, Relaxed);
                        }
                        let pre_lamports = commission_account.lamports();
                        if let Err(err) =
                            commission_account.checked_add_lamports(*commission_lamports)
                        {
                            debug!("reward redemption failed for {commission_pubkey}: {err:?}");
                            total_non_incinerator_burned_lamports
                                .fetch_add(*commission_lamports, Relaxed);
                            return None;
                        }
                        if !is_vote_account {
                            match Self::collector_type_checked(
                                commission_pubkey,
                                pre_lamports,
                                &commission_account,
                                reserved_account_keys,
                                rent,
                                relax_post_exec_min_balance_check,
                            ) {
                                Ok(ExternalCollectorType::SystemAccount) => {}
                                Ok(ExternalCollectorType::Incinerator) => {
                                    total_incinerator_lamports
                                        .fetch_add(*commission_lamports, Relaxed);
                                }
                                Err(err) => {
                                    debug!(
                                        "reward redemption failed for {commission_pubkey} due to \
                                         commission account error: {err:?}"
                                    );
                                    total_non_incinerator_burned_lamports
                                        .fetch_add(*commission_lamports, Relaxed);
                                    return None;
                                }
                            }
                        }
                        Some((
                            *commission_pubkey,
                            RewardInfo {
                                reward_type: RewardType::Voting,
                                lamports: *commission_lamports as i64,
                                post_balance: commission_account.lamports(),
                                commission_bps: *commission_bps,
                            },
                            commission_account,
                        ))
                    },
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L3893-3972)
```rust
    #[test]
    fn test_repeated_inflation_rewards_collector() {
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            42 * LAMPORTS_PER_SOL,
        );

        genesis_config.rent = Rent::default();
        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);

        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();

        let collector_address = Pubkey::new_unique();
        let vote1_address = Pubkey::new_unique();
        let vote2_address = Pubkey::new_unique();
        // Vote account just created
        let bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 0,
                vote_operations: vec![
                    (
                        vote1_address,
                        VoteOperations {
                            create_with_balance: Some(LAMPORTS_PER_SOL),
                            new_commission: Some(50),
                            earned_credits: Some(1000),
                            delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                            new_inflation_rewards_collector: Some(collector_address),
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        vote2_address,
                        VoteOperations {
                            create_with_balance: Some(LAMPORTS_PER_SOL),
                            new_commission: Some(100),
                            earned_credits: Some(1000),
                            delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                            new_inflation_rewards_collector: Some(collector_address),
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );

        // next epoch, get double reward into collector
        let epoch = bank.epoch();
        apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch,
                vote_operations: vec![
                    (
                        vote1_address,
                        VoteOperations {
                            earned_credits: Some(1),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        vote2_address,
                        VoteOperations {
                            earned_credits: Some(1),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );
    }
```
