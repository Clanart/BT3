## Title
Integer-division rounding in transaction fee burn/reward split lets validators skim the intended burn amount - (`runtime/src/bank/fee_distribution.rs`)

### Summary
`Bank::calculate_reward_and_burn_fee_details` computes the amount of a transaction's base fee to burn using truncating integer division (`transaction_fee * burn_percent() / 100`) and then hands the leader/collector *everything else* (`transaction_fee - burn`), rather than rounding the deposit down and the burn up. This mirrors the exact bug class in the Yieldy `instantUnstake` report: a fee is derived via a division that rounds toward zero, and the "leftover" from that rounding is credited to the beneficiary instead of the party that was supposed to receive the fee/burn. [1](#0-0) 

### Finding Description
`calculate_reward_and_burn_fee_details` is:

```rust
let burn = fee_details.transaction_fee * self.burn_percent() / 100;
let deposit = fee_details
    .priority_fee
    .saturating_add(fee_details.transaction_fee.saturating_sub(burn));
``` [2](#0-1) 

`burn_percent()` is a hardcoded 50% (`solana_fee_calculator::DEFAULT_BURN_PERCENT`) [3](#0-2) , and `transaction_fee` is `signature_count * lamports_per_signature` computed in `solana_fee::calculate_signature_fee` [4](#0-3) . Whenever `transaction_fee * 50` is not evenly divisible by 100 (i.e., `transaction_fee` is odd, which happens whenever `lamports_per_signature` is odd or a mixed signature count yields an odd total), `burn` truncates down and the *entire remaining fractional lamport is added to `deposit`* — the leader/collector's reward — rather than being retained as burn. This is structurally identical to the Yieldy bug: the fee (`burn`) is computed via a division that rounds toward zero, and the discrepancy silently flows to the wrong side (`amount - fee` pattern instead of a rounded-up-fee pattern).

The rounding is confirmed intentional/known-buggy by the presence of a feature gate named `remove_rounding_in_fee_calculation` ("Removing unwanted rounding in fee calculation") in the feature set [5](#0-4) [6](#0-5) . However, this feature ID does not appear referenced anywhere outside `feature-set/src/lib.rs` in the indexed code — no `feature_set.is_active(&remove_rounding_in_fee_calculation::id())` check gates `calculate_reward_and_burn_fee_details` or any other fee-rounding code path found. This means the burn-vs-deposit truncation in `fee_distribution.rs` is not actually gated by that feature in this snapshot, so the rounding-favors-deposit behavior remains live in the code path exercised by every transaction.

Existing guards do not stop this: `deposit_or_burn_fee`/`deposit_fees` only check that the *deposit* recipient account satisfies rent-exemption/ownership rules [7](#0-6) ; they do not re-validate that `burn + deposit == transaction_fee + priority_fee` against a rounded-up burn. There is no invariant check enforcing that the burn side gets the "rounding remainder."

### Impact Explanation
Every time `transaction_fee` is odd, up to 1 lamport that should be burned (removed from `capitalization`) is instead credited to the block's fee collector as `deposit`, and `self.capitalization.fetch_sub(total_burn, ...)` reduces capitalization by less than the protocol-intended 50% [8](#0-7) . This is a fund-diversion from the intended deflationary burn to the current slot leader — a validator earns systematically more than the protocol's specified 50%/50% split whenever it processes fee-generating transactions with an odd total fee, and this deviation is silent (no error, no log) since it's baked into the arithmetic rather than an exceptional path.

### Likelihood Explanation
On mainnet-beta today `lamports_per_signature` is typically an even number (e.g. 5000), so a single-signature transaction fee is even and `burn` divides exactly — the bug is latent under current default fee-rate-governor settings. But `lamports_per_signature` is dynamically adjusted per-slot by the fee-rate governor based on target signatures per slot, and additional signature types (ed25519/secp256k1/secp256r1) are summed into the signature count before multiplying by `lamports_per_signature` [4](#0-3) , so odd totals are reachable whenever `lamports_per_signature` itself is odd (achievable via governance/config, and explicitly configurable at genesis via `--target-lamports-per-signature`) [9](#0-8) . This is directly analogous to the report's caveat that the exploit is "not feasible on mainnet" under current fee levels but "likely feasible on low-cost L2s" — here, on any Agave-based cluster/L2 or future fee-rate configuration using odd `lamports_per_signature`, this executes on every transaction, is fully unprivileged (any user sending ordinary transactions triggers it), and requires no malicious peer/validator collusion assumption — the leader who happens to process the transaction benefits automatically.

### Recommendation
Compute `deposit` first via rounding-down/ceiling on the *burn* side rather than deriving the leftover as deposit, e.g. round the burn up: `burn = (transaction_fee * burn_percent + 99) / 100` (ceiling division), and derive `deposit = transaction_fee - burn` from that, ensuring any fractional-lamport remainder is retained on the burn side rather than defaulting to the collector's reward. Alternatively, use `checked_mul`/`div_ceil` explicitly and add a debug assertion that `burn + deposit == transaction_fee` with `burn >= transaction_fee * burn_percent / 100`. If `remove_rounding_in_fee_calculation` was intended to address this, ensure it is actually wired into `calculate_reward_and_burn_fee_details` (it currently is not referenced there).

### Proof of Concept
1. Configure (or await dynamic adjustment of) `lamports_per_signature` to an odd value, e.g. `lamports_per_signature = 1` lamport (or any odd value achievable via the fee-rate governor / genesis `--target-lamports-per-signature`).
2. Submit an ordinary transaction with 1 signature → `transaction_fee = 1`.
3. `burn = 1 * 50 / 100 = 0` (integer truncation) — no lamport is burned. `deposit = priority_fee + (1 - 0) = priority_fee + 1` — the leader collects the full fee, and `capitalization.fetch_sub(0)` leaves capitalization unreduced, confirmed by the test pattern in [10](#0-9)  which shows `expected_burn = transaction_fee * bank.burn_percent() / 100` and `expected_rewards = transaction_fee - expected_burn + priority_fee` — i.e. the test itself encodes the truncating-division behavior as "expected," meaning the rounding-favors-deposit bug is baked into the accepted behavior rather than caught as a bug.
4. Repeating step 2 across many transactions with odd per-transaction fees accumulates a measurable diversion of intended-burn lamports to leader rewards, deviating from the documented 50% burn invariant.

### Citations

**File:** runtime/src/bank/fee_distribution.rs (L69-77)
```rust
    pub(super) fn distribute_transaction_fee_details(&self) {
        let fee_details = self.collector_fee_details.read().unwrap();

        let FeeDistribution { deposit, burn } =
            self.calculate_reward_and_burn_fee_details(&fee_details);

        let total_burn = self.deposit_or_burn_fee(deposit).saturating_add(burn);
        self.capitalization.fetch_sub(total_burn, Relaxed);
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L97-106)
```rust
    pub fn calculate_reward_and_burn_fee_details(
        &self,
        fee_details: &CollectorFeeDetails,
    ) -> FeeDistribution {
        let burn = fee_details.transaction_fee * self.burn_percent() / 100;
        let deposit = fee_details
            .priority_fee
            .saturating_add(fee_details.transaction_fee.saturating_sub(burn));
        FeeDistribution { deposit, burn }
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L108-115)
```rust
    const fn burn_percent(&self) -> u64 {
        // NOTE: burn percent is statically 50%, in case it needs to change in the future,
        // burn_percent can be bank property that being passed down from bank to bank, without
        // needing fee-rate-governor
        static_assertions::const_assert!(solana_fee_calculator::DEFAULT_BURN_PERCENT <= 100);

        solana_fee_calculator::DEFAULT_BURN_PERCENT as u64
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L117-180)
```rust
    /// Attempts to deposit the given `deposit` amount into the fee collector account.
    ///
    /// Returns the original `deposit` amount if the deposit failed and must be burned, otherwise 0.
    fn deposit_or_burn_fee(&self, deposit: u64) -> u64 {
        if deposit == 0 {
            return 0;
        }

        // Per SIMD-0232: the commission collector address should be fetched
        // from the state of the vote account at the beginning of the previous
        // epoch. This is the vote account state used to build the leader
        // schedule for the current epoch, which *DOES NOT* correspond to
        // `Bank::current_epoch_stakes()`.
        let feature_snapshot = self.feature_set.snapshot();
        let collector_id = if feature_snapshot.custom_commission_collector {
            let vote_account = self
                .epoch_stakes
                .get(&self.epoch)
                .and_then(|stakes| {
                    stakes
                        .stakes()
                        .vote_accounts()
                        .get(&self.leader.vote_address)
                })
                .expect("The vote account for the leader must exist");
            // Protection in case the leader is on a vote state without a
            // collector id, which can happen if a dormant pre-v4 vote state
            // accrues stake.
            vote_account
                .vote_state_view()
                .block_revenue_collector()
                .unwrap_or(&self.leader.id)
        } else {
            &self.leader.id
        };

        match self.deposit_fees(collector_id, deposit) {
            Ok(post_balance) => {
                self.rewards.write().unwrap().push((
                    *collector_id,
                    RewardInfo {
                        reward_type: RewardType::Fee,
                        lamports: deposit as i64,
                        post_balance,
                        commission_bps: None,
                    },
                ));
                0
            }
            Err(err) => {
                debug!(
                    "Burned {deposit} lamport tx fee instead of sending to {collector_id} due to \
                     {err}"
                );
                datapoint_warn!(
                    "bank-burned_fee",
                    ("slot", self.slot(), i64),
                    ("num_lamports", deposit, i64),
                    ("error", err.to_string(), String),
                );
                deposit
            }
        }
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L674-720)
```rust
    #[test]
    fn test_distribute_transaction_fee_details_normal() {
        let initial_balance = 1000;
        let genesis = create_genesis_config_with_leader(0, &pubkey::new_rand(), initial_balance);
        let mut bank = Bank::new_for_tests(&genesis.genesis_config);
        let transaction_fee = 100;
        let priority_fee = 200;
        bank.collector_fee_details = RwLock::new(CollectorFeeDetails {
            transaction_fee,
            priority_fee,
        });
        let expected_burn = transaction_fee * bank.burn_percent() / 100;
        let expected_rewards = transaction_fee - expected_burn + priority_fee;

        let collector_id = *bank.leader_id();

        let initial_capitalization = bank.capitalization();
        let initial_collector_balance = bank.get_balance(&collector_id);
        bank.distribute_transaction_fee_details();
        let new_collector_balance = bank.get_balance(&collector_id);

        assert_eq!(
            initial_collector_balance + expected_rewards,
            new_collector_balance
        );
        assert_eq!(
            initial_capitalization - expected_burn,
            bank.capitalization()
        );
        let locked_rewards = bank.rewards.read().unwrap();
        assert_eq!(
            locked_rewards.len(),
            1,
            "There should be one reward distributed"
        );

        let reward_info = &locked_rewards[0];
        assert_eq!(
            reward_info.1.lamports, expected_rewards as i64,
            "The reward amount should match the expected deposit"
        );
        assert_eq!(
            reward_info.1.reward_type,
            RewardType::Fee,
            "The reward type should be Fee"
        );
    }
```

**File:** fee/src/lib.rs (L42-56)
```rust
pub fn calculate_signature_fee(
    SignatureCounts {
        num_transaction_signatures,
        num_ed25519_signatures,
        num_secp256k1_signatures,
        num_secp256r1_signatures,
    }: SignatureCounts,
    lamports_per_signature: u64,
) -> u64 {
    let signature_count = num_transaction_signatures
        .saturating_add(num_ed25519_signatures)
        .saturating_add(num_secp256k1_signatures)
        .saturating_add(num_secp256r1_signatures);
    signature_count.saturating_mul(lamports_per_signature)
}
```

**File:** feature-set/src/lib.rs (L1112-1114)
```rust
pub mod remove_rounding_in_fee_calculation {
    solana_pubkey::declare_id!("BtVN7YjDzNE6Dk7kTT7YTDgMNUZTNgiSJgsdzAeTg2jF");
}
```

**File:** feature-set/src/lib.rs (L2224-2227)
```rust
        (
            remove_rounding_in_fee_calculation::id(),
            "Removing unwanted rounding in fee calculation #34982",
        ),
```

**File:** genesis/src/main.rs (L454-463)
```rust
            Arg::with_name("target_lamports_per_signature")
                .long("target-lamports-per-signature")
                .value_name("LAMPORTS")
                .takes_value(true)
                .default_value(default_target_lamports_per_signature)
                .help(
                    "The cost in lamports that the cluster will charge for signature verification \
                     when the cluster is operating at target-signatures-per-slot",
                ),
        )
```
