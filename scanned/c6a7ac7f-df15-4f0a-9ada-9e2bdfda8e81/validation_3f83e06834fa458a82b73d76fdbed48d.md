### Title
Vote commission truncation from bps to percent can silently zero out a validator's inflation-reward commission - (`File: vote/src/vote_state_view/field_frames.rs`)

### Summary
`VoteStateV4` stores inflation-reward commission as `inflation_rewards_commission_bps: u16` (basis points, granularity 0.01%). Several consumers, however, still read commission through the legacy `u8` percent accessor `VoteStateView::commission()`, which converts bps → percent by integer division by 100. Any bps value below 100 (i.e. any commission strictly less than 1%) truncates to `0`, exactly like Panoptic's `_poolFee = fee / 100` truncating any Uniswap fee under 0.01% to zero.

### Finding Description
`CommissionView::commission_percent()` performs the truncating conversion: [1](#0-0) 

`VoteStateView::commission()` (the public, widely-used getter) forwards directly to this truncating conversion for V4 accounts: [2](#0-1) 

The dedicated non-truncating accessor `inflation_rewards_commission()` (returns full `u16` bps) exists side-by-side: [3](#0-2) 

The test suite itself documents the truncation-to-zero behavior as expected: bps values of `0` map to percent `0`, and the comment on the general clamp test shows `(bps/100)` semantics are considered "correct": [4](#0-3) 

A validator/vote account owner can legitimately set `inflation_rewards_commission_bps` to any value in `[1, 99]` (e.g. 50 bps = 0.5%) via `set_inflation_rewards_commission_bps`, which the vote program accepts without a lower bound: [5](#0-4) 

Any code path that reads `.commission()` (the truncating `u8` accessor) instead of `.inflation_rewards_commission()`/`inflation_rewards_commission_bps` will observe `0%` commission even though the account holder configured a nonzero commission, and any downstream logic gated on "is commission zero" (display, eligibility checks, or reward-split selection where `commission_rate_in_basis_points` is false) is fed a corrupted value. This mirrors the Panoptic bug precisely: a fixed, non-parameterized `DECIMALS`-style divisor (`100`) applied to a fine-grained governance-configurable rate silently zeroes out the intended fee/commission for values below the divisor's resolution.

Call sites confirmed to invoke `.commission()` outside of tests include `programs/vote/src/vote_processor.rs`, `programs/vote/src/vote_state/mod.rs`, `rpc/src/rpc.rs`, and `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` [6](#0-5) . I was not able to fully read the exact logic inside `calculation.rs` in this pass to confirm whether the truncated result there feeds directly into an actual lamport reward split (versus only being used for reporting/metrics), so the ultimate blast radius of this specific call site is unconfirmed and would need direct file inspection to establish definitively.

### Impact Explanation
If a consensus- or reward-affecting code path uses the truncating `commission()` getter instead of the bps-accurate `inflation_rewards_commission()`, a vote account with a legitimate sub-1% commission (e.g. 0.99% = 99 bps) would be treated as having 0% commission, i.e. the voter would receive none of the commission they configured, and the staker would receive an amount they weren't entitled to. This is a fund-mismapping bug matching the "broken functionality" class (Medium in the original C4 report) rather than a full fund-theft primitive, since the disputed lamports still go to a legitimate stake-reward recipient (the staker rather than the voter) instead of being lost or stolen by an outside attacker.

### Likelihood Explanation
Likelihood depends entirely on whether any live, currently-active reward/consensus code path calls the truncating `.commission()` getter on `u16`-bps V4 accounts rather than the bps-preserving accessor. The vote program itself already supports commission values with 0.01% granularity [5](#0-4) , so any validator operator setting a sub-1% commission is sufficient to trigger the truncation — no attacker or malicious peer is required, matching an "unprivileged, always-reachable" trigger condition. However, I could not confirm from the excerpts gathered whether the reward-calculation path (`commission_split`/`commission_split_preserve_lamports` in `runtime/src/inflation_rewards/mod.rs`) is actually driven by the truncated `commission()` value or exclusively by `inflation_rewards_commission_bps`/`commission_bps()`; the `commission_split` functions I inspected take a `u16` bps parameter directly [7](#0-6) , suggesting the reward-splitting math itself may already use full-precision bps and NOT be affected. This uncertainty is the main gap preventing a definitive severity rating.

### Recommendation
Audit every call site of `VoteStateView::commission()` to confirm none of them feed into actual lamport-affecting logic (reward splits, fee assessments, eligibility thresholds tied to nonzero commission). Where full precision matters, always use `inflation_rewards_commission()` (bps) rather than the truncating `commission()` percent accessor. Consider deprecating/renaming `commission()` to make its lossy nature explicit (e.g. `commission_percent_lossy()`), and add a debug assertion or lint rule to catch new usages of the truncating variant in reward-critical code.

### Proof of Concept
1. Create/update a `VoteStateV4` account and call `set_inflation_rewards_commission_bps(50)` (0.5% commission) — accepted without restriction per [5](#0-4) .
2. Read the account through `VoteStateView::commission()`: it returns `0` per `commission_percent()`'s `bps / 100` division [1](#0-0) , even though `inflation_rewards_commission()` correctly returns `50`.
3. Any downstream consumer that branches on `commission() == 0` (e.g., "does this voter charge any commission?") incorrectly treats a 0.5%-commission voter as a 0%-commission voter, silently redirecting the commission's lamports to the staker side instead of the voter.

Because I could not conclusively trace whether the specific reward-distribution arithmetic (`commission_split`) is driven by the lossy `u8` accessor or the lossless `u16` bps field within the tool budget available, this should be treated as a confirmed *code smell / latent bug* with unconfirmed exploitability in the reward-payout path — a Devin session with full file access should trace all `.commission()` call sites listed above to close this gap.

### Citations

**File:** vote/src/vote_state_view/field_frames.rs (L320-330)
```rust
impl CommissionView<'_> {
    pub(super) fn commission_percent(&self) -> u8 {
        if !self.frame.use_bps {
            self.buffer[0]
        } else {
            let data = unsafe { *(self.buffer.as_ptr() as *const [u8; 2]) };
            let bps = u16::from_le_bytes(data);
            let percent = (bps / 100).min(u8::MAX as u16);
            percent as u8
        }
    }
```

**File:** vote/src/vote_state_view.rs (L88-91)
```rust
    pub fn commission(&self) -> u8 {
        self.inflation_rewards_commission_view()
            .commission_percent()
    }
```

**File:** vote/src/vote_state_view.rs (L109-111)
```rust
    pub fn inflation_rewards_commission(&self) -> u16 {
        self.inflation_rewards_commission_view().commission_bps()
    }
```

**File:** vote/src/vote_state_view.rs (L825-844)
```rust
    fn test_vote_state_view_v4_commission_clamps() {
        // The VoteStateView commission() getter must clamp values
        // > u8::MAX to u8::MAX for V4 accounts with large bps.
        for (bps, expected) in [
            (0u16, 0u8),
            (10_000, 100),
            (25_500, 255),
            (25_600, 255),
            (u16::MAX, 255),
        ] {
            let state = VoteStateV4 {
                inflation_rewards_commission_bps: bps,
                ..VoteStateV4::default()
            };
            let versioned = VoteStateVersions::new_v4(state);
            let buf = Arc::new(bincode::serialize(&versioned).unwrap());
            let view = VoteStateView::try_new(buf).unwrap();
            assert_eq!(view.commission(), expected);
        }
    }
```

**File:** programs/vote/src/vote_state/handler.rs (L1759-1774)
```rust
        // First test some "normal" values.
        for bps in [0, 100, 500, 1_000, 5_000, 10_000] {
            handler.set_inflation_rewards_commission_bps(bps);
            let v4 = handler.as_ref_v4();
            assert_eq!(v4.inflation_rewards_commission_bps, bps);
            // commission() should return bps / 100
            assert_eq!(handler.commission(), (bps / 100) as u8);
        }

        // Now test values > 10,000 are allowed at program level.
        // Capping happens during reward calculation, not storage.
        for bps in [10_001, 15_000, u16::MAX] {
            handler.set_inflation_rewards_commission_bps(bps);
            let v4 = handler.as_ref_v4();
            assert_eq!(v4.inflation_rewards_commission_bps, bps);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1-1)
```rust
use {
```

**File:** runtime/src/inflation_rewards/mod.rs (L377-406)
```rust
fn commission_split(commission_bps: u16, on: u64) -> (u64, u64, bool) {
    const MAX_BPS: u16 = 10_000;
    const MAX_BPS_U128: u128 = MAX_BPS as u128;
    match commission_bps.min(MAX_BPS) {
        0 => (0, on, false),
        MAX_BPS => (on, 0, false),
        split => {
            let on = u128::from(on);
            // Calculate mine and theirs independently and symmetrically instead of
            // using the remainder of the other to treat them strictly equally.
            // In Tower, this is also to cancel the rewarding if either of the parties
            // should receive only fractional lamports, resulting in not being rewarded at all.
            // Thus, note that we intentionally discard any residual fractional lamports.
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

            (mine as u64, theirs as u64, true)
        }
    }
}
```
