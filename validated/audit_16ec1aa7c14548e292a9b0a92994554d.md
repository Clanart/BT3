## Title
Single missing/reorganized validator entry aborts voting-reward crediting for the entire block via `RewardStateError::MissingRewardSlotValidator` - (File: `runtime/src/block_component_processor/vote_reward.rs`)

## Summary
`calc_vote_rewards_update_vote_states` iterates over the full set of validators named in a reward certificate and, for each one, calls `RewardState::calculate_reward`, which does a hard lookup of the validator in the reward-slot's vote-account snapshot [1](#0-0) . If any single validator pubkey referenced by the certificate is not found in that snapshot, the function returns `RewardStateError::MissingRewardSlotValidator` [2](#0-1) , which is propagated with `?` all the way out of `update_account` → `update_accounts` → `calc_vote_rewards_update_vote_states` [3](#0-2) . This mirrors the external report's pattern exactly: a per-entity operation inside a batch loop that is supposed to process *every* validator instead fails outright and aborts the whole batch because of one bad entry.

## Finding Description
The code's own doc comments make the invariant explicit: `CalcVoteRewardUpdateVoteStatesError` and `RewardStateError` are annotated with "These errors should cause the processing of the bank to fail" [4](#0-3)  and [5](#0-4) . This is the same broken invariant as the Solidity report: an aggregate/batch update (`generatePerformance` there, `calc_vote_rewards_update_vote_states` here) that must succeed for *all* participants is short-circuited by a single participant's failure inside a `for` loop.

In `update_accounts`, the loop over `validators` calls `RewardState::update_account`, which calls `self.calculate_reward(...)?` and propagates any error via the `?` operator inside the match arms [6](#0-5) . Unlike other lookups in the same file that fail *soft* (e.g. `VoteState::try_new` simply logs and `continue`s past unparsable/missing vote accounts [7](#0-6) ), the `MissingRewardSlotValidator` path fails *hard* for the entire batch instead of skipping just that one validator, even though the surrounding code already demonstrates the pattern of gracefully skipping missing/invalid entries.

Because `reward_validators` (the certificate's validator set) is compared against `accounts`, a snapshot of the vote-account stakes as of `reward_slot` [8](#0-7) , any situation where a certified validator's vote account cannot be resolved in that particular epoch-stakes snapshot (e.g., due to stale/short-lived epoch-stakes retention, closed vote accounts, or validators that rotate out of the stake set between when the certificate was formed and when it's replayed) triggers this hard failure for the whole reward-crediting pass, not just for that one validator.

## Impact Explanation
Per the crate's own error semantics, this class of error is intended to fail bank processing entirely rather than skip the single bad entry [4](#0-3) . In the leader-rewards/voting-reward crediting path, this is not an isolated Solidity-style transaction revert that a caller can retry later — it is invoked during block/bank processing, so a single validator that cannot be resolved against the reward-slot's vote-account snapshot can block reward crediting for the affected bank, which is a false-execution/processing-halt risk on the core reward-accounting path rather than a benign, retryable failure.

## Likelihood Explanation
Likelihood is low-to-moderate and requires no malicious actor: it depends purely on ordinary state divergence between when a reward certificate names a set of validators and when the corresponding reward-slot vote-account snapshot is looked up during replay (e.g., a validator's vote account is closed, or epoch-stakes for that slot are no longer retained/valid), analogous to the "validator has active rebalance request" scenario in the original report being a routine, non-adversarial condition that becomes increasingly likely as the validator set churns.

## Recommendation
Treat a validator missing from the reward-slot snapshot the same way `VoteState::try_new` treats missing/unparsable vote accounts elsewhere in this file — log and skip that single validator's reward rather than propagating `RewardStateError` and aborting the entire `update_accounts` batch [7](#0-6) . This preserves the invariant that other validators' rewards are still credited even if one entry cannot be resolved, avoiding the same all-or-nothing batch failure described in the original report.

## Proof of Concept
Conceptually: 
1. A reward certificate (`ValidatedRewardCert`) names validator V among `reward_validators`.
2. By the time `calc_vote_rewards_update_vote_states` processes the certificate against the bank's `epoch_stakes_from_slot(reward_slot)` snapshot, V's vote account is absent from that snapshot's `vote_accounts()` map (e.g., it was closed or is not present in the retained epoch-stakes data) [8](#0-7) .
3. `RewardState::calculate_reward(V, ...)` returns `Err(RewardStateError::MissingRewardSlotValidator { pubkey: V, .. })` [2](#0-1) .
4. This `?`-propagates out of `update_account` (line 274) and `update_accounts` (line 391) as `Err(CalcVoteRewardUpdateVoteStatesError::RewardState(...))`, aborting the entire reward-update pass instead of only skipping V's reward [9](#0-8) .

I was not able to inspect the exact call site in `runtime/src/block_component_processor.rs` (how the returned `Err` is ultimately handled — panic vs. bank marked dead vs. other) within the available tool budget, so the precise blast radius (single-bank failure vs. broader consensus halt) could not be fully confirmed from the index and would need direct inspection of that file to verify.

### Citations

**File:** runtime/src/block_component_processor/vote_reward.rs (L24-33)
```rust
/// Different types of errors that can happen when calculating and paying voting reward.
///
/// These errors should cause the processing of the bank to fail.
#[derive(Debug, Error)]
pub enum CalcVoteRewardUpdateVoteStatesError {
    #[error("allocating accounts failed with {0}")]
    AllocateAccounts(#[from] AllocateAccountsError),
    #[error("Processing reward state failed with {0}")]
    RewardState(#[from] RewardStateError),
}
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L46-70)
```rust
/// Different types of error that happen when looking up state to process the reward cert.
///
/// These errors should cause the processing of the bank to fail.
#[derive(Debug, Error)]
pub enum RewardStateError {
    #[error("missing epoch stakes for reward_slot {reward_slot} in bank_slot {bank_slot}")]
    MissingEpochStakes { reward_slot: Slot, bank_slot: Slot },
    #[error("missing EpochInflationAccountState for bank_slot {bank_slot}")]
    MissingEpochInflationAccountState { bank_slot: Slot },
    #[error(
        "missing validator stake info for reward epoch {reward_epoch} in bank_slot {bank_slot}"
    )]
    NoEpochValidatorStake {
        reward_epoch: Epoch,
        bank_slot: Slot,
    },
    #[error("validator {pubkey} missing in bank_slot {bank_slot} for reward slot {reward_slot}")]
    MissingRewardSlotValidator {
        pubkey: Pubkey,
        reward_slot: Slot,
        bank_slot: Slot,
    },
    #[error("genesis cert not found. reward_slot={reward_slot}; bank_slot={bank_slot}")]
    GenesisCertNotFound { reward_slot: Slot, bank_slot: Slot },
}
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L88-109)
```rust
    fn try_new(
        vote_accounts: &HashMap<Pubkey, (u64, VoteAccount)>,
        vote_pubkey: Pubkey,
    ) -> Option<Self> {
        let Some((_, account)) = vote_accounts.get(&vote_pubkey) else {
            info!("did not find vote account for vote_pubkey={vote_pubkey}");
            return None;
        };
        let versions = match bincode::deserialize(account.account().data()) {
            Ok(s) => s,
            Err(e) => {
                info!("bincode::deserialize for vote_pubkey={vote_pubkey} failed with {e}");
                return None;
            }
        };
        let handler = match VoteStateHandler::try_new_from_vote_state_versions(versions) {
            Ok(h) => h,
            Err(e) => {
                info!("VoteStateHandler::try_new() for vote_pubkey={vote_pubkey} failed with {e}");
                return None;
            }
        };
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L191-198)
```rust
        let epoch_stakes = bank.epoch_stakes_from_slot(reward_slot).ok_or(
            RewardStateError::MissingEpochStakes {
                reward_slot,
                bank_slot,
            },
        )?;
        let accounts = epoch_stakes.stakes().vote_accounts().as_ref();
        let total_stake = epoch_stakes.total_stake();
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L239-259)
```rust
    fn calculate_reward(
        &self,
        validator: Pubkey,
        accumulating_leader_reward: &mut u64,
    ) -> Result<u64, RewardStateError> {
        let (reward_slot_validator_stake, _) =
            self.accounts
                .get(&validator)
                .ok_or(RewardStateError::MissingRewardSlotValidator {
                    pubkey: validator,
                    reward_slot: self.reward_slot,
                    bank_slot: self.bank_slot,
                })?;
        let (validator_reward, leader_reward) = calculate_reward(
            &self.epoch_inflation_state,
            self.total_stake,
            *reward_slot_validator_stake,
        );
        *accumulating_leader_reward = accumulating_leader_reward.saturating_add(leader_reward);
        Ok(validator_reward)
    }
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L271-287)
```rust
        if self.reward_validators.contains(&vote_state.vote_pubkey) {
            self.update_votes(vote_state);
            let reward =
                self.calculate_reward(vote_state.vote_pubkey, accumulating_leader_reward)?;
            if let Some(reward) = NonZero::new(reward) {
                increment_credits(
                    vote_state.handler.epoch_credits_mut(),
                    self.migration_epoch,
                    self.current_epoch,
                    reward,
                );
            };
            Ok(true)
        } else {
            Ok(false)
        }
    }
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L385-399)
```rust
    for validator in validators {
        let Some(mut vote_state) = VoteState::try_new(vote_accounts, validator) else {
            continue;
        };
        let account_updated = match (reward_state, final_cert_state) {
            (None, None) => false,
            (Some(state), None) => state.update_account(&mut vote_state, &mut leader_reward)?,
            (None, Some(state)) => state.update_account(&mut vote_state),
            (Some(reward_state), Some(final_state)) => {
                let reward_updated =
                    reward_state.update_account(&mut vote_state, &mut leader_reward)?;
                let final_cert_updated = final_state.update_account(&mut vote_state);
                reward_updated || final_cert_updated
            }
        };
```
