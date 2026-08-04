## Title
Unauthenticated `BandwidthManager.purchase()` lets anyone evict a victim app's paid bandwidth subscriptions via cheap FIFO-cap spam - (File: `evm/src/apps/BandwidthManager.sol`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`BandwidthManager.purchase()` is a fully public, permissionless entrypoint that lets **any caller** credit bandwidth to **any `(chain, app)` pair** — this is the documented "sponsorship" feature. On the pallet side, `push_subscription` appends every purchase to a strict FIFO list capped at `MAX_SUBSCRIPTIONS = 1024`; once full, the *oldest* entry is unconditionally evicted regardless of its remaining value. Because nothing ties `msg.sender`/payer to the target `app`, and eviction is purely FIFO with no value-weighting, an attacker can spend a small number of cheap, minimal-tier purchases to evict a victim's legitimately-purchased, high-value subscriptions from the queue — permanently destroying paid-for bandwidth entitlement and degrading/denying the victim app's ability to dispatch ISMP messages. This mirrors the external report's core primitive: a cheap, structurally-valid action that corrupts a shared queue/floor to the detriment of a legitimate, higher-value participant.

### Finding Description
`purchase()` has no authorization check linking the caller to the `app` parameter: [1](#0-0) 

The docs confirm this is intentional "multi-tenant" sponsorship — anyone can pay to credit any app on any chain: [2](#0-1) 

On the pallet side, every purchase (regardless of payer) appends to the same `(app_chain, app)` FIFO list, capped at 1024 entries. When full, `push_subscription` evicts the head unconditionally: [3](#0-2) 

The cap and eviction semantics are also explicitly acknowledged in docs and governance notes, but the assumption is that eviction only occurs under "pathological repeat-buy behavior" by the *same* app — not adversarial third-party spam targeting someone else's bucket: [4](#0-3) [5](#0-4) 

Critically, eviction is **not** value-aware: the gate/queue has no concept of "don't evict a large, unexpired, high-remaining-bytes subscription in favor of a newly-purchased minimal one." `push_subscription` always evicts `list.remove(0)` — whichever is oldest — irrespective of how many bytes/duration it still holds relative to the incoming purchase: [6](#0-5) 

This is exactly the report's bug class: a public, low-cost action (placing a bid / making a tiny purchase) permanently and cheaply displaces a legitimate, higher-value entry in a shared, capped, order-sensitive structure that the victim relied on.

### Impact Explanation
An attacker who knows (or observes on-chain) that a target app's `(chain, app)` subscription queue is near the 1024 cap can call `purchase()` repeatedly with the cheapest configured tier (`TierOne`, 1 month) to fill the remaining slots and then continue evicting the victim's real, unexpired, high-value subscriptions one by one. Each eviction destroys bytes/duration the victim already paid for (`SubscriptionEvicted` fires with the lost bytes), and once the queue is fully attacker-controlled, the victim's app can be pushed toward `GateError::NoAllowance`/`Insufficient`, blocking its ISMP dispatches (a governance-relevant app like a token gateway could be denied service). This is a direct loss of already-paid-for funds/entitlement and an unauthorized-execution/logic attack against a production pallet component (`pallet-bandwidth`) and its EVM counterpart (`BandwidthManager.sol`), matching the bounty's "stealing or loss of funds" / "logic attacks" categories.

### Likelihood Explanation
The attack requires no privileged role, no malicious relayer/prover, and no front-running — it is a straightforward sequence of ordinary `purchase()` calls funded by the attacker's own tokens, exploiting the intentionally-permissionless sponsorship design plus the value-blind FIFO eviction. The only cost gate is the attacker's own tier price × number of evictions needed, which can be small relative to the value being destroyed (e.g., evicting `TierFour`, multi-month subscriptions using a cheap `TierOne` purchase per eviction). Any app operating close to the 1024-entry cap (achievable over time even under normal top-up cadences, or accelerated by the attacker's own spam) is exposed.

### Recommendation
- Restrict `purchase()`'s crediting to only work for callers that are either the `app` itself or on an app-controlled allowlist of authorized sponsors, rather than allowing arbitrary third parties to write into any `(chain, app)` bucket.
- Make eviction value-aware: when the FIFO list is full, refuse the push (reverting the purchase) or evict based on lowest remaining economic value (`remaining_bytes` × time-to-expiry) rather than unconditionally evicting the oldest entry.
- Alternatively, increase `MAX_SUBSCRIPTIONS` materially or key subscription capacity per-payer within an app's bucket so one payer's purchases cannot force out another payer's entries.
- At minimum, emit stronger observability/alerting and consider refunding or crediting back a pro-rated amount to the original purchaser when their subscription is evicted early, to bound the loss.

### Proof of Concept
1. Victim's app `X` on chain `EVM-8453` has purchased several `TierFour` (largest, longest) subscriptions over time via `BandwidthManager.purchase(app=X, tier=4, months=12, chain="EVM-8453")`, filling most of the 1024-entry FIFO in `Allowance::<T>::get(EVM-8453, X)`.
2. Attacker, with no relationship to `X`, calls `BandwidthManager.purchase(app=X, tier=1, months=1, chain="EVM-8453")` repeatedly (cheapest tier, `evm/src/apps/BandwidthManager.sol:148`). Each call succeeds — there is no check binding `msg.sender` to `app`.
3. Each purchase message is delivered to `pallet-bandwidth::on_accept`, which calls `push_subscription` (`modules/pallets/bandwidth/src/lib.rs:404-437`). Once `X`'s list hits 1024, every further attacker purchase evicts the oldest entry — i.e., one of `X`'s legitimate, still-valuable `TierFour` subscriptions — emitting `SubscriptionEvicted` with the lost bytes.
4. Repeating this enough times evicts all of `X`'s real allocation, replacing it with the attacker's minimal `TierOne` entries. `X`'s paid-for bandwidth is destroyed at a fraction of its original cost, and depending on timing, `X` can be pushed into `GateError::Insufficient`/`NoAllowance`, blocking its ISMP dispatches through `BandwidthGate::try_consume` (`modules/pallets/bandwidth/src/lib.rs:509-565`).

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L148-164)
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

```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```

**File:** modules/pallets/bandwidth/src/lib.rs (L400-437)
```rust
		/// Append a fresh subscription with a fixed expiry. If the list
		/// is already at `MaxSubscriptions`, evict the oldest entry and
		/// emit [`Event::SubscriptionEvicted`] so the lost bytes are
		/// auditable. Returns the new subscription's `expires_at`.
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

			expires_at
		}
```

**File:** docs/content/developers/evm/bandwidth/governance.mdx (L98-103)
```text
        duration_secs: 30 * 24 * 3600,
    },
);
```

Unlike a real purchase, the `tier` field here is **just a label** — it doesn't have to match a configured `TierConfig`. This is the admin escape hatch: refunds for bad purchases, credits during a runtime migration, one-off grants. The same FIFO/cap rules apply — pushing onto a full list evicts the oldest entry with `SubscriptionEvicted`.
```
