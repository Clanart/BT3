### Title
Inconsistent rounding in commission bps→percent conversion causes stake-reward commission miscalculation - (File: `vote/src/vote_state_view/field_frames.rs`)

### Summary
Analogous to the WERC721/Bridge percentage-unit mismatch, Agave's vote-state layer has two disagreeing conventions for converting the native `inflation_rewards_commission_bps` field (basis points, introduced by SIMD-0185/SIMD-0291) back into a legacy `u8` percentage. The actual implementation truncates (`bps / 100`), while the documented/expected conversion (in the RPC response type docstring) is `bps.div_ceil(100)`. Because reward distribution can, depending on a feature gate, recompute `commission_bps` from this truncated percentage (`percent * 100`), a validator's real commission is silently reduced whenever its native bps value is not an exact multiple of 100 — exactly the "significantly lower than intended" pattern from the WERC721 report, but here it misallocates real lamports between validator commission and staker rewards.

### Finding Description
`CommissionView::commission_percent()` converts the stored bps value to a percent using floor division: [1](#0-0) 

This is the only implementation of the bps→percent accessor discovered in the vote-state view layer, and it is used by `VoteStateView::commission()` for V4 accounts (`CommissionFrame::new_bps()` selects this path): [2](#0-1) 

However, the documented contract for this exact conversion, as recorded on the public RPC type `RpcVoteAccountInfo`, states the conversion should be `bps.div_ceil(100).min(255)`: [3](#0-2) 

SIMD-0291 explicitly permits storing an arbitrary `u16` basis-points commission (not necessarily a multiple of 100), as confirmed by the test suite: [4](#0-3) 

The truncated percent value is then fed back into the reward-calculation path whenever the `commission_rate_in_basis_points` feature is not yet active (or is being phased in / delayed via `delay_commission_updates`), reconstructing `commission_bps` as `percent * 100`: [5](#0-4) 

This value is passed directly into `redeem_rewards`, which splits `PointValue` rewards between the stake account and the vote/commission-collector account: [6](#0-5) 

The broken invariant: the native, authoritative value of a validator's commission is `inflation_rewards_commission_bps` (u16, arbitrary granularity per SIMD-0291), but the legacy code path re-derives `commission_bps` from a lossily-truncated `u8` percent, rounding down instead of up (or exactly) as documented. Unlike the WERC721 bug (which used two different fixed scale conventions, 0.001 ether vs 0.01 ether, that never matched), this Agave analog is a rounding-direction mismatch between the documented conversion and the actual code, applied at a boundary where fractional-percent commissions are a sanctioned, feature-gated state (SIMD-0291).

### Impact Explanation
Every time a vote account's `inflation_rewards_commission_bps` is not an exact multiple of 100 (e.g., `199` bps = 1.99%, a value explicitly allowed since SIMD-0291), and the legacy (non-bps) reward-calculation branch is taken (`commission_rate_in_basis_points` feature inactive, or `delay_commission_updates` forces use of a snapshot vote state read through the legacy accessor), the effective commission bps used for the epoch's reward split is rounded down to `floor(bps/100) * 100`. For `199` bps this yields `100` bps — roughly half the validator's actual configured commission. This misallocates real lamports at every affected epoch boundary: the validator/commission-collector under-collects commission and the delegated stakers over-collect rewards (or vice versa depending on which side of a rounding boundary the intended value sits), a direct analog to the "royalty significantly lower than intended" impact in the source report. Because this operates inside `redeem_delegation_rewards`/`calculate_stake_rewards_and_commissions`, which determine actual lamport transfers during epoch reward distribution, this is a fund-miscalculation bug in `runtime` reward accounting rather than a cosmetic RPC display issue.

### Likelihood Explanation
This requires no malicious actor: it triggers purely from a validator setting a non-round-percent commission via SIMD-0291 combined with the legacy (percent-based) commission code path still being exercised — which the code explicitly supports via the `commission_rate_in_basis_points` feature flag and the `delay_commission_updates` snapshot lookup, both of which read through `vote_state.commission()`/`vote_state_for_commission.commission()`. As long as any epoch's reward computation takes the non-bps branch while a validator has a fractional-percent bps commission set, the discrepancy manifests automatically and deterministically every such epoch.

### Recommendation
Make the bps→percent conversion in `CommissionView::commission_percent()` consistent with the documented contract, and audit every place that reconstructs `commission_bps` from the legacy `u8` percent (in particular the `commission_rate_in_basis_points`-gated branches in `redeem_delegation_rewards`) to ensure they either always operate on the native `inflation_rewards_commission_bps` field directly, or use the same, single, well-tested rounding rule (`div_ceil` or otherwise) everywhere so that no epoch's commission split is computed from a lossily-truncated intermediate value.

### Proof of Concept
1. Enable SIMD-0291 (v4 vote state, `commission_rate_in_basis_points` feature not yet active or `delay_commission_updates` true) and set a vote account's `inflation_rewards_commission_bps = 199` via `set_inflation_rewards_commission_bps` [7](#0-6) .
2. At epoch boundary, `redeem_delegation_rewards` reads `vote_state.commission()`, which internally calls `CommissionView::commission_percent()` → `floor(199/100) = 1` [1](#0-0) .
3. The legacy branch reconstructs `commission_bps = 1 * 100 = 100` instead of the intended `199` [8](#0-7) .
4. `redeem_rewards` splits epoch rewards using `100` bps instead of `199` bps, permanently under-crediting the commission-collector's lamports for that epoch relative to the validator's actually configured rate — the "royalty" (here: commission) collected is roughly half of what was intended, mirroring the external report's impact pattern.

Note: I could not directly view the body of `VoteStateHandler::commission()`/`VoteStateView::commission()` beyond the `field_frames.rs` implementation and test assertions (only exact-multiple-of-100 cases like `10_000→100`, `25_500→255` were exercised in the visible tests), so I cannot 100% confirm from the index that no other rounding is applied elsewhere before this value reaches `field_frames.rs`. Given index size limits, a Devin session with full repo access would be needed to trace every call site of `commission()` exhaustively and confirm there is no compensating rounding logic upstream.

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

**File:** vote/src/vote_state_view.rs (L302-308)
```rust
    fn commission_frame(&self) -> CommissionFrame {
        match &self {
            Self::V1_14_11(_) => CommissionFrame::new_percent(),
            Self::V3(_) => CommissionFrame::new_percent(),
            Self::V4(_) => CommissionFrame::new_bps(),
        }
    }
```

**File:** rpc-client-types/src/response.rs (L406-422)
```rust
    /// The current stake, in lamports, delegated to this vote account
    pub activated_stake: u64,

    /// An 8-bit unsigned integer used as a fraction (commission/100) for
    /// rewards payout. Before SIMD-0291 activation, this is the native
    /// commission value. After activation, this is derived from basis
    /// points with: `bps.div_ceil(100).min(255)`.
    pub commission: u8,

    /// A 16-bit unsigned integer used as the raw basis points for rewards
    /// payout. Before SIMD-0291 activation, this is derived from the
    /// percentage commission: `percent * 100`. After activation, this is the
    /// native basis points value stored in vote state.
    ///
    /// Note: Field is `None` when querying a node that predates this field.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inflation_rewards_commission_bps: Option<u16>,
```

**File:** programs/vote/src/vote_state/handler.rs (L153-158)
```rust
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    pub(crate) fn set_inflation_rewards_commission_bps(&mut self, commission_bps: u16) {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => v4.inflation_rewards_commission_bps = commission_bps,
        }
    }
```

**File:** programs/vote/src/vote_state/handler.rs (L1379-1399)
```rust
    #[test]
    fn test_v4_commission_getter_clamps_extreme_bps() {
        // Verify commission() clamps to u8::MAX for extreme bps values
        // instead of wrapping around. SIMD-0291 allows storing any u16 as
        // inflation_rewards_commission_bps — the legacy getter must not
        // produce misleading values.
        for (bps, expected) in [
            (10_000, 100),   // normal
            (25_500, 255),   // exact boundary, no clamping needed
            (25_501, 255),   // just past boundary, clamped
            (25_600, 255),   // would wrap to 0 without clamping
            (u16::MAX, 255), // would wrap to 143 without clamping
        ] {
            let vote_state = VoteStateV4 {
                inflation_rewards_commission_bps: bps,
                ..Default::default()
            };
            let handler = VoteStateHandler::new_v4(vote_state);
            assert_eq!(handler.commission(), expected);
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L703-724)
```rust
        // Fetch the voter commission from past epochs to attempt to
        // delay the effect of commission updates by at least one
        // full epoch.
        // When `commission_rate_in_basis_points` is true, use the new field
        // `inflation_rewards_commission_bps`; otherwise use the legacy
        // percentage field and convert to basis points by multiplying by 100.
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L726-763)
```rust
        match redeem_rewards(
            stake,
            commission_bps,
            DelegatedVoteState::from(vote_state),
            CalculationEnvironment {
                rewarded_epoch,
                point_value,
                stake_history,
                new_rate_activation_epoch,
                commission_rate_in_basis_points,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            },
            reward_calc_tracer,
            ag_epoch_type,
            current_lamports,
            minimum_lamports,
        ) {
            Ok((stake_reward, commission_lamports, stake)) => {
                let inflation = InflationReward {
                    stake,
                    stake_reward,
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                };
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
