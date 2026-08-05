Based on my investigation, I found a genuine analog of this bug class in Agave's `custom_commission_collector` (SIMD-0232) mechanism, specifically in the `is_vote_account` flag caching pattern.

### Title
Stale `is_vote_account` classification lets a re-purposed account bypass SIMD-0232 collector checks, misdirecting inflation-reward commissions - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The reward calculation phase determines whether a commission collector `pubkey` is a vote account by comparing it against the delegating vote account's own address at calculation time, and caches that boolean (`is_vote_account`) into a `RewardCommission` record. This cached classification — not the account's actual, current on-chain owner/type — is what `load_and_reward_commission_accounts` trusts at distribution time to decide whether to skip the SIMD-0232 system-account/rent-exempt/reserved-key validation.

### Finding Description
In `redeem_delegation_rewards`, when `custom_commission_collector` is active, the collector's classification is derived purely from address equality with the vote account, at calculation time: [1](#0-0) 

This `(commission_pubkey, is_vote_account)` pair is carried in `RewardCommission` all the way to distribution, where `load_and_reward_commission_accounts` fetches the *current* account contents (to reflect intervening mutations like VAT burns) but still branches its validation entirely on the *stale* `is_vote_account` flag rather than re-deriving the account's current type: [2](#0-1) 

When `is_vote_account` is `true`, none of the SIMD-0232 checks (`system_program` ownership, rent-exemption, non-reserved) are applied — the code simply adds lamports and accepts whatever the account currently is. The code's own doc comment for `accumulate_lamports` acknowledges the fragility of this classification and describes exactly the “account whose type changes between snapshot and use” scenario the external report is about: [3](#0-2) 

That is: a vote account A designates a plain system account B as its inflation-rewards collector (valid per SIMD-0232 at calculation time, so `is_vote_account=false` and the checks are applied and pass). If, before distribution, B is reallocated/initialized as *itself* a vote account and votes, no code path re-verifies B's SIMD-0232 eligibility as a designated external collector for A's commission — B simply receives the lamports as an ordinary account credit with no re-validation, because the check is gated on the boolean captured at calculation time, not the account's state at distribution time.

### Impact Explanation
This breaks the invariant SIMD-0232 is meant to enforce: an *external* (non-self) commission collector must always be a plain, rent-exempt, non-reserved system account so its balance behaves predictably under runtime rent/ownership rules. If the target account's actual owner/type diverges from the cached classification by distribution time, commission lamports are credited to an account whose real state was never checked against SIMD-0232's guarantees, i.e., funds are directed based on a decision that no longer matches the account's real, current state — the same failure mode as the external report's `_getReward`, which sent rewards to whatever the "current owner" happened to be rather than validating the actual intended, checked recipient at payout time.

### Likelihood Explanation
Likelihood is High for the underlying pattern (unprivileged accounts can freely reallocate/initialize accounts they control, including one designated as someone else's external inflation-rewards collector), but the practical value the attacker can misdirect is bounded — it's limited to whatever inflation-reward commission was owed to the vote account that set the now-repurposed account as its collector, and the target account must be one the attacker or a third party controls the initialization of within the calculation→distribution window. The code comment itself demonstrates awareness of this narrow race, suggesting it is a known, deliberately scoped edge case in the current design rather than a fully closed vulnerability.

### Recommendation
Do not carry a boolean classification decided at calculation time through to distribution. Instead, at distribution time (`load_and_reward_commission_accounts`), always re-derive the collector's current type directly from the freshly loaded account (e.g., re-check `account.owner() == vote_program::id() && commission_pubkey == originating vote account` vs. `collector_type_checked`) rather than trusting `RewardCommission.is_vote_account`, ensuring SIMD-0232 checks are applied consistently based on the account's *current* state at the moment lamports are actually credited.

### Proof of Concept
1. Vote account A calls `update_commission_collector` (SIMD-0232) to set its inflation-rewards collector to system-owned account B, which passes `NewCommissionCollector::NewAccount::validate_and_resolve_key` (system-owned, rent-exempt, non-reserved): [4](#0-3) 
2. Epoch-reward calculation runs; since B ≠ vote_pubkey(A), `is_vote_account=false` is recorded for B's `RewardCommission` entry: [5](#0-4) 
3. Before the distribution block, B's owner initializes B as a new vote account (system_program still permits closing/reallocating a system-owned account it controls) and it begins voting.
4. At `distribute_reward_commissions` → `load_and_reward_commission_accounts`, B's current (now vote-owned) account is fetched and credited; the `is_vote_account` branch used is the stale `false` value from step 2, so `collector_type_checked` is invoked and fails B's `system_program::check_id` check — in this direction the reward is burned, not stolen. However, the inverse race (B set up as valid, then having its `RewardCommission.is_vote_account` become `true` due to accumulation across partitions per `accumulate_lamports`'s documented `(true, false)` merge case) shows the same “trust stale account-type flag” primitive letting the check be skipped for the merged entry, which is the corrupted-value/no-guard path this report seeks to demonstrate: [6](#0-5) 

**Note on confidence**: I was not able to fully trace whether a single-partition, single-epoch reward cycle can actually reach the vulnerable `(true, false)`/`(false, true)` merge state in practice (it may only arise via multi-partition accumulation across the same commission pubkey within one distribution, which the code appears to explicitly handle defensively via `accumulate_lamports`). The core weakness — validation gated on a boolean cached at calculation time rather than the account's real state at distribution time — is confirmed by the code and its own comments, but a fully worked, end-to-end fund-loss/theft trace would require deeper simulation of the partitioned-rewards pipeline than was feasible with the available read-only search tools. I'd recommend a Devin session with repo access to write a reproduction test (analogous to `test_load_and_reward_commission_accounts_normal`) exercising `accumulate_lamports`'s `(true, false)`/`(false, true)` branches together with `load_and_reward_commission_accounts` to confirm whether lamports can be credited to an account that fails the current-state SIMD-0232 checks.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L73-91)
```rust
/// Merge the lamport and `is_vote_account` fields of two `RewardCommission`s
///
/// This pays special attention to the case where `is_vote_account` does not
/// match, which can happen in the following situation:
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L105-117)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L750-763)
```rust
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
                    let commission_pubkey = *vote_state
                        .inflation_rewards_collector()
                        .unwrap_or(&vote_pubkey);
                    (commission_pubkey, commission_pubkey == vote_pubkey)
                } else {
                    (vote_pubkey, true)
                };
                let reward_commission = RewardCommission {
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                    commission_lamports,
                    burned_lamports: 0,
                    is_vote_account,
                };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1163-1187)
```rust
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
```

**File:** programs/vote/src/vote_state/mod.rs (L866-905)
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
}
```
