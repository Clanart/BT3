### Title
VAT vote-account inclusion gate keyed off raw, freely-withdrawable lamport balance can be trivially gamed to bypass SIMD-357 minimum-balance filtering - ([File: vote/src/vote_account.rs])

### Summary
`ConnextPriceOracle.getPriceFromDex` derived a price from a live, ungated `balanceOf` query on an AMM pool, which any actor could pump/donate into right before the read and drain afterward, corrupting the derived value. The Agave analog is `VoteAccounts::clone_and_filter_for_vat`, which gates inclusion of a vote account in the "Vote Account Truncation" (VAT, SIMD-357) distribution set on a raw, point-in-time `vote_account.lamports()` check against `minimum_vote_account_balance`, rather than any locked/committed collateral. Because vote-account lamports above the rent-exempt reserve are freely and immediately withdrawable by the authorized withdrawer (no lockup, unlike stake), this balance check can be satisfied only momentarily.

### Finding Description
`clone_and_filter_for_vat` computes `has_balance = vote_account.lamports() >= minimum_vote_account_balance` directly from the live account state and uses this to decide whether a vote account participates in the VAT-filtered set: [1](#0-0) 

This filtered set becomes `filtered_distribution_vote_accounts`, which is used both to compute reward distribution and — critically — to seed the *next epoch's* `EpochStakes` when no epoch-stake snapshot yet exists for the boundary epoch: [2](#0-1) [3](#0-2) 

`EpochStakes` in turn is the source for the BLS-pubkey-to-rank map used in Alpenglow voting and for leader-schedule computation: [4](#0-3) 

The lamport balance that gates this filter is not a locked or bonded value — the vote program's `withdraw` instruction lets the authorized withdrawer pull out any lamports above the rent-exempt minimum (and above `pending_delegator_rewards`) at any time, with no epoch delay or cooldown, unlike delegated stake which is subject to warmup/cooldown via `stake_history`: [5](#0-4) 

Because the epoch-boundary computation (`compute_new_epoch_caches_and_rewards` → `update_epoch_stakes`) runs at a deterministic slot derived from `EpochSchedule`, a vote-account operator (or anyone who can direct a `system_instruction::transfer` to the vote account's pubkey, since crediting lamports to any account requires no ownership check) knows in advance exactly which slot will read `vote_account.lamports()` for the VAT filter. They can top up the vote account just before that slot to clear `minimum_vote_account_balance_for_vat` and withdraw the excess immediately after, exactly mirroring the Uniswap donate-then-absorb pattern from the source report, but here the "absorb" step is simply calling `Withdraw` back to their own wallet since there is no AMM-style skim mechanism needed.

### Impact Explanation
This defeats the stated purpose of the SIMD-357 balance floor — excluding under-funded/dust vote accounts from the fixed-size VAT distribution list (`MAX_ALPENGLOW_VOTE_ACCOUNTS`) and, transitively, from the `EpochStakes` snapshot that seeds BLS-rank mapping and leader-schedule inputs. Since inclusion in the truncated set is otherwise sorted/capped by stake, a manipulated balance check does not directly forge stake weight, but it can let a vote account cross the eligibility floor and consume a slot in the capped `max_vote_accounts` list (potentially displacing another vote account near the boundary), or avoid whatever downstream restrictions are gated on VAT membership, without maintaining the real economic backing the floor is meant to require. This is a false-acceptance issue into a consensus-relevant set (bounded severity relative to the report's "fund theft," since the mechanism does not directly transfer other users' funds, but it corrupts a value load-bearing for consensus set membership).

### Likelihood Explanation
Likelihood is moderate-to-high for any node operator: the epoch-boundary slot is publicly computable from `EpochSchedule`, the only capability required is being the vote account's authorized withdrawer (or simply anyone able to send a `system_instruction::transfer` to top up the balance temporarily, since crediting lamports needs no owner authorization), and the cost is only a single transient transfer plus a `Withdraw` transaction fee. No malicious peer/validator assumption beyond ordinary transaction submission is required.

### Recommendation
Do not gate VAT/consensus-relevant inclusion on the instantaneous `lamports()` balance of a freely-withdrawable account. Either (a) derive eligibility from a value that cannot be pumped-and-drained within a single slot/epoch window — e.g., require the balance to have been continuously above the threshold for some minimum duration, or (b) tie the floor to a value with the same lockup semantics as `stake` (subject to warmup/cooldown via `StakeHistory`), or (c) require the excess balance be similarly locked/committed for at least one epoch before being counted toward `minimum_vote_account_balance_for_vat`.

### Proof of Concept
1. Operator controls a vote account `V` with `authorized_withdrawer = W` and balance below `minimum_vote_account_balance_for_vat` (rent-exempt minimum only).
2. Using `EpochSchedule`, compute the exact slot at which `Bank::update_epoch_stakes`/`compute_new_epoch_caches_and_rewards` will next run (`leader_schedule_epoch` boundary).
3. Shortly before that slot, submit a `system_instruction::transfer` from any funded account into `V`, raising `V.lamports()` above `minimum_vote_account_balance_for_vat` — this succeeds because Solana's System transfer does not require the destination to be owned by the sender or by any particular program.
4. `clone_and_filter_for_vat` at the epoch boundary computes `has_balance = true` for `V`, so `V` is included in `filtered_distribution_vote_accounts`, which is used both for reward calculation and to seed `EpochStakes` for the leader-schedule epoch: [6](#0-5) [7](#0-6) 
5. Once past the snapshot slot, `W` calls `VoteInstruction::Withdraw` to pull the donated lamports back down to the rent-exempt minimum, using the withdraw path's rent-exempt check, which permits withdrawing everything above the reserve: [8](#0-7) 
6. `V` has been counted as VAT-eligible for the entire epoch despite never holding the required balance except for the single slot of the snapshot read, repeatable every epoch at negligible cost.

### Citations

**File:** vote/src/vote_account.rs (L212-231)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
```

**File:** runtime/src/bank.rs (L1781-1792)
```rust
        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, &filtered_distribution_vote_accounts);
```

**File:** runtime/src/bank.rs (L2613-2624)
```rust
            let stakes = match prefiltered_distribution_vote_accounts {
                Some(prefiltered) => Stakes::new(prefiltered, self.epoch()),
                None => self.get_top_epoch_stakes(),
            };
            let stakes = SerdeStakesToStakeFormat::from(stakes);
            let new_epoch_stakes = VersionedEpochStakes::new(stakes, leader_schedule_epoch);
            info!(
                "new epoch stakes, epoch: {}, total_stake: {}",
                leader_schedule_epoch,
                new_epoch_stakes.total_stake(),
            );

```

**File:** runtime/src/epoch_stakes.rs (L349-360)
```rust
    pub fn bls_pubkey_to_rank_map(&self) -> &Arc<BLSPubkeyToRankMap> {
        match self {
            Self::Current {
                bls_pubkey_to_rank_map,
                ..
            } => bls_pubkey_to_rank_map.get_or_init(|| {
                Arc::new(BLSPubkeyToRankMap::new(
                    self.stakes().vote_accounts().as_ref(),
                ))
            }),
        }
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L1079-1121)
```rust
    let remaining_balance = vote_account
        .get_lamports()
        .checked_sub(lamports)
        .ok_or(InstructionError::InsufficientFunds)?;

    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
```
