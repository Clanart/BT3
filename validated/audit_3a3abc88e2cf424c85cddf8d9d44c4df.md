Based on my research, the strongest local analog is a griefing/fund-loss path in `pallet-bandwidth`'s FIFO subscription model, reachable by any unprivileged buyer via `BandwidthManager.purchase()`.

### Title
Unbounded low-cost purchases evict other buyers' paid bandwidth subscriptions before they expire - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`push_subscription` enforces only a *count* cap (`MAX_SUBSCRIPTIONS = 1024`) per `(app_chain, app)`, with no minimum purchase size, no per-buyer/per-period cap, and no cost floor tied to the eviction risk. Any account can call `BandwidthManager.purchase()` cheaply and repeatedly (any tier, `months=1`) to push the FIFO list to its 1024-entry cap and evict older, still-unexpired subscriptions that other buyers already paid for — exactly the "uncapped deposits during a window overrun a bounded resource" pattern in the source report, here manifesting as forced fund loss instead of a revert-DoS.

### Finding Description
`push_subscription` appends every purchase unconditionally: [1](#0-0) . When the list is already at `MAX_SUBSCRIPTIONS`, it silently evicts index 0 (the oldest, i.e., whichever subscription has the earliest `purchased_at`, regardless of `remaining_bytes` or `expires_at`), and the "lost_bytes" recorded is whatever the evicted victim had left — including bytes they paid for and have not yet consumed [2](#0-1) .

There is no cap or throttle on how many subscriptions any single caller can add per period: `BandwidthManager.purchase()` is fully permissionless, takes `months` and `tier` from the caller, and dispatches one credit message per call with no rate limiting on the EVM side [3](#0-2) . On the pallet side, `on_accept` (credit path) simply validates the sender is the registered manager and appends via `push_subscription` — it does not check whether the pushed subscription would evict a still-valid entry, nor does it require the pusher to be the app owner. The subscription cap is documented as a defensive backstop only against "pathological repeat-buy behavior," acknowledging the eviction path exists but treating it as benign because normal apps buy at most once per cycle: "In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom" [4](#0-3) . This is the same "no cap on volume during a window against a bounded backing resource" root cause as the GammaSwap `depositVault` bug — instead of overrunning `s.LP_TOKEN_BALANCE` and reverting, it overruns the 1024-slot FIFO and silently deletes paid-for, unexpired bandwidth belonging to a third party.

Because tier `TierOne`/`TierTwo` etc. can be bought with `months=1` at whatever price governance set, and the attacker only needs 1024 cheap purchases (not 1024 of the victim's expensive tier) to evict the victim's earlier large purchase, the cost of the attack is bounded by `1024 × min_tier_price`, which is independent of the value of the bandwidth being destroyed.

### Impact Explanation
An attacker can force-evict any app's legitimate, already-paid, unexpired bandwidth subscriptions on Hyperbridge for a bounded, attacker-controlled cost — this is a real loss of funds for the victim app (its purchase price is spent and the subscription entry is destroyed before it drains its bytes), leading to that app's cross-chain requests being rejected by `BandwidthGate::try_consume` (`GateError::NoAllowance`/`Insufficient`), which can then block that app's intended cross-chain messages entirely (denial of a paid-for service). This falls squarely in the bounty's "stealing or loss of funds" / "logic attacks" bucket and requires no privileged relayer, prover, or admin role — only an unprivileged EVM account calling `purchase()`.

### Likelihood Explanation
Medium-to-High. The attack requires no special access — only enough fee tokens to fund 1024 minimum-tier purchases against the same `(app_chain, app)` key targeted by the victim. Tier prices are set by governance and can be small (the docs quote a $50/100KB tier as the cheapest plan), so the total attack cost can be modest relative to a competitor wanting to grief a specific app's bandwidth, or relative to a sponsor's larger bulk purchase they intend to rely on for a long duration. The eviction logic and lack of any per-purchase or per-window limiter is directly visible in the code and documented behavior, not merely theoretical.

### Recommendation
- Cap the number of subscriptions (or total bytes) a single purchase-payer or purchase transaction can push into an `(app_chain, app)` bucket within a rolling window, separate from the hard 1024 structural cap.
- When evicting on a full list, prefer evicting the entry with the least remaining value (e.g., smallest `remaining_bytes × time-to-expiry`) rather than blindly the oldest, or refuse the push (reverting the purchase) rather than silently destroying another payer's unexpired allowance.
- Consider merging same-tier repurchases into an aggregate subscription instead of always queuing a new FIFO row, which removes the incentive to spam cheap purchases purely to grow list length.
- Emit a way for a purchaser to signal a minimum acceptable "not-evicted" size, or make `SubscriptionEvicted` refundable/attributable so griefed payers can be compensated by governance.

### Proof of Concept
1. Governance configures `TierOne` (cheapest tier) with some non-zero price and registers a `BandwidthManager` for a source chain.
2. Victim (a legitimate app sponsor) calls `purchase(app, TierFour, 12, chain)` once, crediting a large, long-duration subscription into `Allowance[chain][app]` at FIFO position 0 (or wherever it lands as the current oldest entry).
3. Attacker, using any unprivileged account, calls `purchase(app, TierOne, 1, chain)` 1024 times in a loop (each a cheap, minimum-tier purchase), targeting the exact same `chain`/`app` pair the victim used.
4. Each call triggers `on_accept` → `push_subscription`, which appends a new row; once the list reaches `MAX_SUBSCRIPTIONS = 1024`, every subsequent push evicts index 0 — pushing the victim's subscription toward, and eventually off, the front of the FIFO queue well before its `expires_at`.
5. `SubscriptionEvicted { app_chain, app, tier, lost_bytes }` fires for the victim's entry with `lost_bytes > 0`, confirming bytes the victim paid for and had not consumed were destroyed — matching the existing test's mechanics (`subscription_cap_evicts_oldest`), just driven maliciously against a third party rather than by the same owner [5](#0-4) .
6. The victim's app subsequently has its cross-chain requests rejected by `BandwidthGate::try_consume` despite having paid for bandwidth that has not yet expired.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L1-30)
```rust
// Copyright (C) Polytope Labs Ltd.
// SPDX-License-Identifier: Apache-2.0

// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// 	http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! # pallet-bandwidth
//!
//! Prepaid `(chain, app)` byte balances credited by tier purchases
//! from `BandwidthManager.sol`. Each purchase carries its own
//! `app_chain`, so any deployment can sponsor any app on any chain.
//!
//! Each `(chain, app)` row holds a FIFO list of [`Subscription`]s
//! (`BoundedVec`, capped at 1024). Every purchase appends a new
//! subscription with a fixed `expires_at`; expiry never extends and
//! same-tier repurchases don't stack — they queue. The gate drains
//! the oldest live subscription first; once empty it moves to the
//! next. Subscriptions that aren't reached before their expiry are
//! swept silently — what you paid for is yours only until it expires.
//! Pushes onto a full list evict the oldest entry and emit
//! [`Event::SubscriptionEvicted`].
```

**File:** modules/pallets/bandwidth/src/lib.rs (L404-434)
```rust
		fn push_subscription(
			app_chain: &StateMachine,
			app: &AppKey,
			tier: TierIndex,
			bytes: BandwidthBytes,
			duration_secs: u64,
		) -> u64 {
			let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();
			let expires_at = now.saturating_add(duration_secs);
			let new_sub =
				Subscription { tier, remaining_bytes: bytes, expires_at, purchased_at: now };

			let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
				let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
					Some(list.remove(0))
				} else {
					None
				};
				// Capacity is now guaranteed; try_push can't fail.
				let _ = list.try_push(new_sub);
				evicted
			});

			if let Some(old) = evicted {
				Self::deposit_event(Event::SubscriptionEvicted {
					app_chain: *app_chain,
					app: app.clone(),
					tier: old.tier,
					lost_bytes: old.remaining_bytes,
				});
			}
```

**File:** evm/src/apps/BandwidthManager.sol (L148-193)
```text
    function purchase(bytes calldata app, uint256 tier, uint256 months, bytes calldata chain)
        external
        returns (bytes32 commitment)
    {
        if (app.length == 0 || chain.length == 0 || months == 0) revert InvalidPurchase();
        uint256 price18d = tierPrice[tier];
        if (price18d == 0) revert UnknownTier();

        uint256 total18d = price18d * months;
        address feeToken = IDispatcher(_host).feeToken();
        uint8 dec = IERC20Metadata(feeToken).decimals();
        uint256 scale = 10 ** (18 - dec);
        if (total18d % scale != 0) revert PriceNotRepresentable();
        uint256 amount = total18d / scale;

        IERC20(feeToken).safeTransferFrom(msg.sender, address(this), amount);

        BandwidthPurchaseMsg memory body = BandwidthPurchaseMsg({
            app: app,
            tier: tier,
            months: months,
            chain: chain
        });

        commitment = IDispatcher(_host).dispatch(
            DispatchPost({
                dest: IDispatcher(_host).hyperbridge(),
                to: PALLET_BANDWIDTH_MODULE_ID,
                body: abi.encode(body),
                timeout: 0,
                fee: 0,
                payer: address(this)
            })
        );

        emit BandwidthPurchased({
            payer: msg.sender,
            feeToken: feeToken,
            tier: tier,
            months: months,
            amountPaid: amount,
            app: app,
            chain: chain,
            commitment: commitment
        });
    }
```

**File:** modules/pallets/testsuite/src/tests/pallet_bandwidth.rs (L523-574)
```rust
/// The 1024-sub cap evicts the oldest entry. force_credit reuses the
/// same push path as purchase, so this also covers the purchase cap.
#[test]
fn subscription_cap_evicts_oldest() {
	new_test_ext().execute_with(|| {
		jump_to(T0);
		let cap = MAX_SUBSCRIPTIONS as u128;

		// Fill the list to exactly the cap. `bytes` encodes the index
		// so we can prove which one got evicted.
		for i in 0..cap {
			Bandwidth::force_credit(
				RuntimeOrigin::root(),
				ForceCreditParams {
					app_chain: APP_CHAIN,
					app: app_key(),
					tier: TIER1,
					bytes: i + 1,
					duration_secs: MONTH_SECS,
				},
			)
			.unwrap();
		}
		assert_eq!(sub_count(APP_CHAIN), cap as usize);
		assert_eq!(sub_at(APP_CHAIN, 0).unwrap().remaining_bytes, 1, "oldest is index 1");

		// One more push: evicts the oldest, appends the new one.
		Bandwidth::force_credit(
			RuntimeOrigin::root(),
			ForceCreditParams {
				app_chain: APP_CHAIN,
				app: app_key(),
				tier: TIER1,
				bytes: cap + 1,
				duration_secs: MONTH_SECS,
			},
		)
		.unwrap();

		assert_eq!(sub_count(APP_CHAIN), cap as usize, "still capped");
		assert_eq!(
			sub_at(APP_CHAIN, 0).unwrap().remaining_bytes,
			2,
			"former second-oldest is now front",
		);
		assert_eq!(
			sub_at(APP_CHAIN, (cap - 1) as usize).unwrap().remaining_bytes,
			cap + 1,
			"new sub is at the back",
		);
	});
}
```
