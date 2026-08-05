Confirmed: this workspace has `[profile.release]` with no `overflow-checks = true` override, so release builds use Rust's default (`overflow-checks = false` in release), meaning `runtime/src/bank/fee_distribution.rs:101` computes `fee_details.transaction_fee * self.burn_percent() / 100` with wrapping (silent) semantics in production validator builds, while every other line in the same function/file uses `saturating_add`/`saturating_sub`. This is the direct Agave analog of the GEB "not using SafeMath" report: raw `*`/`/` operators mixed into an otherwise-guarded fee/lamports accounting path.

### Title
Unchecked multiplication in transaction-fee burn calculation can wrap and desynchronize bank capitalization - (File: `runtime/src/bank/fee_distribution.rs`)

### Summary
`Bank::calculate_reward_and_burn_fee_details` computes the burned portion of collected transaction fees using raw Solidity-style arithmetic operators (`*`, `/`) instead of the `checked_*`/`saturating_*` helpers used everywhere else in this file and in the sibling `CollectorFeeDetails::accumulate`/`total_transaction_fee` methods [1](#0-0) . Because the workspace `[profile.release]` does not set `overflow-checks = true` [2](#0-1) , this multiplication silently wraps instead of panicking in production validator binaries, unlike the debug-only `.expect(...)`-guarded paths elsewhere in the reward code (e.g. `commission_split`) [3](#0-2) .

### Finding Description
`fee_details.transaction_fee` is a `u64` accumulated across every transaction fee collected in a slot via `CollectorFeeDetails::accumulate`, itself built with `saturating_add` [4](#0-3) . When the bank later distributes/burns fees for the slot, it computes:

```rust
let burn = fee_details.transaction_fee * self.burn_percent() / 100;
```

`self.burn_percent()` is a constant 50 [5](#0-4) . If `transaction_fee` exceeds `u64::MAX / 50` (~3.69 × 10^17), the multiplication overflows. In this workspace's release profile, overflow checks are off, so the multiply wraps modulo 2^64 rather than panicking, producing an arbitrary, incorrect `burn` value. This value then flows into:

```rust
let deposit = fee_details.priority_fee.saturating_add(fee_details.transaction_fee.saturating_sub(burn));
```

and into `distribute_transaction_fee_details`, which does `self.capitalization.fetch_sub(total_burn, Relaxed)` [6](#0-5) . A wrapped `burn` can make `deposit`/`burn` inconsistent with the real accumulated fee, directly corrupting `Bank::capitalization` — the value the runtime treats as ground truth for total lamports in existence, cross-checked elsewhere by capitalization-overflow assertions in `accounts_db` [7](#0-6) .

Existing guards do not stop this path: `accumulate()` only saturates the *sum* of fees, not the later multiply; `deposit_or_burn_fee` and `distribute_transaction_fee_details` operate purely on already-computed `deposit`/`burn` and assume they were derived correctly.

### Impact Explanation
If reachable, this corrupts `Bank::capitalization`, an unprivileged-transaction-driven value used for supply accounting and sanity checks throughout the runtime (fund loss/creation class, matching the "false execution/rooting/acceptance" impact bucket). Because all validators execute the identical, deterministic wrapping arithmetic on the identical accumulated `transaction_fee` for a given block, this would not by itself fork the cluster (it's not a validator-version-dependent behavior in this codebase, since the profile is workspace-wide) — but it does silently miscompute a security- and economics-critical fund-accounting value with no `SafeMath`-style protection at the one place that most needs it, mirroring the exact bug class in the report.

### Likelihood Explanation
Low-to-uncertain. `transaction_fee` is bounded in practice by `signature_count * lamports_per_signature` summed with `saturating_add` across all transactions in a single slot; reaching `> u64::MAX / 50 ≈ 3.69 × 10^17` lamports (~369 million SOL-equivalent) of *base* signature fees in one slot would require an extraordinary, likely economically/consensus-infeasible number of signatures/lamports-per-signature, given total lamport supply is on a similar order of magnitude. I could not fully verify from the available index whether any code path (e.g., `lamports_per_signature` governor updates, or fee-rate-governor manipulation) can push this value into the overflow range within a single slot — this bounds analysis is the main uncertainty. Absent that, likelihood is speculative rather than confirmed.

### Recommendation
Replace the raw arithmetic with the same `checked_mul`/`saturating_mul` and `checked_div`/`saturating_div` (or explicit `u128` intermediate, mirroring `commission_split`'s pattern) used throughout the rest of this file and `inflation_rewards/mod.rs`, e.g.:
```rust
let burn = (fee_details.transaction_fee as u128 * self.burn_percent() as u128 / 100) as u64;
```
or `fee_details.transaction_fee.saturating_mul(self.burn_percent()) / 100`, so the burn calculation cannot silently wrap regardless of build profile.

### Proof of Concept
Not independently reproducible from local static analysis alone: exploiting this requires accumulating a single-slot `CollectorFeeDetails::transaction_fee` above `u64::MAX / 50`, and I was unable to confirm from the indexed code whether any legitimate or attacker-controlled path can push `lamports_per_signature * total_signatures` that high within one slot. This is flagged as an uncertainty rather than a demonstrated exploit; a Devin session with full repo/test access could construct a targeted unit test (mirroring `test_load_and_reward_commission_accounts_overflow`/`test_calculate_capitalization_overflow_intra_slot`) that seeds `collector_fee_details` near `u64::MAX` and asserts on wrap-around behavior of `calculate_reward_and_burn_fee_details` to confirm reachability.

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

**File:** Cargo.toml (L605-607)
```text
[profile.release]
split-debuginfo = "unpacked"
lto = "thin"
```

**File:** runtime/src/inflation_rewards/mod.rs (L390-401)
```rust
            let mine = on
                .checked_mul(u128::from(split))
                .expect("multiplication of a u64 and u16 should not overflow")
                / MAX_BPS_U128;
            let theirs = on
                .checked_mul(u128::from(
                    MAX_BPS
                        .checked_sub(split)
                        .expect("commission cannot be greater than MAX_BPS"),
                ))
                .expect("multiplication of a u64 and u16 should not overflow")
                / MAX_BPS_U128;
```

**File:** runtime/src/bank.rs (L295-303)
```rust
impl CollectorFeeDetails {
    pub(crate) fn accumulate(&mut self, fee_details: &FeeDetails) {
        self.transaction_fee = self
            .transaction_fee
            .saturating_add(fee_details.transaction_fee());
        self.priority_fee = self
            .priority_fee
            .saturating_add(fee_details.prioritization_fee());
    }
```

**File:** accounts-db/src/accounts_db.rs (L4761-4797)
```rust
                    .try_fold(0, u64::checked_add)
            })
            .try_reduce(|| 0, u64::checked_add)
            .expect("capitalization cannot overflow");

        // Sum as i128 because there is potential (although unlikely) for the cache updates to
        // overflow i64::MAX. For example, if the cache has multiple transactions that transfer a
        // large amount of lamports from one account to another, it could sum all of the transfers
        // from accounts first, overflow i128. Wrapping logic could also handle this properly (ie.
        // come to the correct answer), but then detection of overflow would be broken.
        let cached_update = self
            .accounts_cache
            .cached_pubkeys()
            .iter()
            .map(|pubkey| {
                // subtract out whatever older version the index walk produced (if any)
                let stored_lamports = stored_lamports(pubkey);

                // add in the cached amount of lamports
                let cached_lamports = self
                    .load(
                        ancestors,
                        pubkey,
                        LoadHint::FixedMaxRoot,
                        PopulateReadCache::False,
                    )
                    .map(|(account, _slot)| account.lamports())
                    .unwrap_or(0);

                cached_lamports as i128 - stored_lamports as i128
            })
            .sum::<i128>();

        i128::from(storage_capitialization)
            .checked_add(cached_update)
            .and_then(|result| u64::try_from(result).ok())
            .expect("capitalization cannot overflow")
```
