### Title
Permissionless `purchase()` lets anyone flood any app's bandwidth queue and evict its already-paid allowance - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`BandwidthManager.purchase()` on EVM and `pallet-bandwidth::on_accept` on Hyperbridge enforce only `months > 0` and a non-zero tier price — there is no minimum purchase size, minimum cost, or per-caller restriction on which `(app_chain, app)` bucket gets credited. Because the FIFO subscription list is capped at 1024 entries and evicts the *oldest* entry unconditionally on overflow, an attacker can repeatedly buy the cheapest possible tier (`months = 1`, lowest-priced `TierIndex`) into a victim app's bucket to push out the victim's legitimate, larger, longer-lived subscriptions — destroying bandwidth the victim already paid for. This is the same "no minimum deposit ⇒ dust entries that undermine the system's economic assumptions" bug class as the source report, applied to Hyperbridge's bandwidth-accounting ledger instead of a lending vault.

### Finding Description
`purchase()` in `evm/src/apps/BandwidthManager.sol` only checks: [1](#0-0) 

`app.length == 0 || chain.length == 0 || months == 0` and `price18d == 0` — there is no minimum `months`, no minimum USD cost, and critically **no restriction on which `app`/`chain` the caller credits**. Any address can call `purchase()` targeting *any* victim app on *any* chain (this is the intentional "sponsorship" feature documented in `docs/content/developers/evm/bandwidth/overview.mdx:108-114`).

On the Hyperbridge side, `on_accept` in `modules/pallets/bandwidth/src/lib.rs:455-489` decodes the purchase and calls `push_subscription`, which mutates the `Allowance` map keyed only by `(app_chain, app)`: [2](#0-1) 

The `SubscriptionList` is a `BoundedVec` capped at `MAX_SUBSCRIPTIONS` (1024). When full, `push_subscription` unconditionally evicts the entry at index 0 — the oldest subscription, following FIFO drain order — regardless of who paid for it or how much bandwidth it held:

```rust
let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
    Some(list.remove(0))
} else {
    None
};
let _ = list.try_push(new_sub);
```

Since the bucket is keyed only by `(app_chain, app)` and not by payer, a legitimate customer who bought a large, long-duration tier for their app has no protection against a third party — completely uninvolved in the relationship — spamming 1024 minimum-cost, minimum-duration purchases into that same `(app_chain, app)` bucket. Each spam purchase costs only the cheapest configured tier price for `months = 1`; there is no floor preventing this from being economically trivial. Once the queue is saturated with dust subscriptions, the next legitimate purchase (or the attacker's own dust purchases) evicts real, previously-paid allowance, which is silently and permanently lost (only surfaced via `SubscriptionEvicted { lost_bytes }`, an audit event, not a remedy).

The docs implicitly assume "normal" usage patterns ("at the default of one purchase per cycle, 1024 buys is years of headroom" — `docs/content/developers/evm/bandwidth/overview.mdx:77`), which is precisely the assumption the external report's "no minimum deposit size" flags as unsafe against an adversarial actor.

### Impact Explanation
A victim app that has paid for bandwidth (potentially a large multi-month/multi-year purchase) can have that paid allowance evicted and destroyed by an unrelated, permissionless attacker for a fraction of the cost, because:
1. `purchase()` allows crediting an arbitrary `app`/`chain` pair the caller does not control (`evm/src/apps/BandwidthManager.sol:148-193`).
2. There is no minimum purchase cost/duration to make spam prohibitively expensive.
3. `push_subscription`'s eviction (`modules/pallets/bandwidth/src/lib.rs:416-425`) removes the oldest entry with no regard to size or payer.

This is a direct loss of funds (the fee token paid for the evicted allowance is gone with nothing to show for it) and, if the app's bandwidth is drained entirely, a denial of the bridge's message-relay gate (`BandwidthGate::try_consume`) for that app, since the router rejects requests once `NoAllowance`/`Insufficient` fires (`modules/pallets/bandwidth/src/lib.rs:509-564`).

### Likelihood Explanation
Medium. `purchase()` is a fully public, unprivileged entrypoint with no allowlist, KYC, or cooldown, and governance is only guaranteed to configure *some* non-zero tier price — nothing prevents a cheap tier from existing (e.g. a low-priced `TierOne`). An attacker only needs to know a victim's `app` identifier (public information visible in `BandwidthCredited`/`OrderPlaced`-style events) and repeatedly call `purchase()` 1024 times, which is inexpensive in aggregate given no floor on tier price × months.

### Recommendation
Introduce a minimum purchase cost/duration (a protocol-level floor on `tier.price × months`) so dust purchases are not economically trivial, and/or restrict eviction so a purchase cannot displace another payer's unexpired subscription with materially more remaining value/duration — e.g., require the evicted entry to have less remaining value than the incoming one, or key/rate-limit `push_subscription` per payer rather than allowing an arbitrary third party to freely mutate another app's queue. At minimum, expose a governance-configurable minimum `months` per tier and consider capping how many entries a single payer can contribute to another party's `(app_chain, app)` bucket within a time window.

### Proof of Concept
1. Governance configures `TierOne` with the smallest legal price and `duration_secs`/`bytes` (`set_tier`).
2. Victim buys a large purchase for `AppKey = X` on `chain = C` (e.g. `tier=TierFour`, `months=24`), landing subscription #1 in `Allowance[C][X]`.
3. Attacker (unrelated third party, no relationship to X) calls `BandwidthManager.purchase(app = X, tier = TierOne, months = 1, chain = C)` 1024 times (any account, minimal fee-token cost each).
4. Once `Allowance[C][X].len() == 1024`, each subsequent attacker purchase triggers `push_subscription`'s eviction path (`modules/pallets/bandwidth/src/lib.rs:416-425`), removing the oldest entry from index 0.
5. Because FIFO order preserves insertion order and the victim's subscription was inserted first, it is evicted before it can be consumed by the gate, emitting `SubscriptionEvicted { lost_bytes: <victim's paid bytes> }` — the victim's paid-for bandwidth is destroyed for the cost of 1024 minimum-tier purchases to the attacker.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L152-161)
```text
        if (app.length == 0 || chain.length == 0 || months == 0) revert InvalidPurchase();
        uint256 price18d = tierPrice[tier];
        if (price18d == 0) revert UnknownTier();

        uint256 total18d = price18d * months;
        address feeToken = IDispatcher(_host).feeToken();
        uint8 dec = IERC20Metadata(feeToken).decimals();
        uint256 scale = 10 ** (18 - dec);
        if (total18d % scale != 0) revert PriceNotRepresentable();
        uint256 amount = total18d / scale;
```

**File:** modules/pallets/bandwidth/src/lib.rs (L404-437)
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

			expires_at
		}
```
