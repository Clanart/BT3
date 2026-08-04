Confirmed: `purchase()` in `BandwidthManager.sol` places no constraint tying the `chain`/`app` targeting to the caller's own asset, and `push_subscription` in `modules/pallets/bandwidth/src/lib.rs` evicts the oldest FIFO entry once a `(chain, app)` bucket hits `MAX_SUBSCRIPTIONS` (1024), with no per-buyer accounting or minimum-size guard.

### Title
Unauthenticated `BandwidthManager.purchase()` lets any payer evict another buyer's paid, unconsumed bandwidth subscription via FIFO-cap griefing - (File: `evm/src/apps/BandwidthManager.sol`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
The external report's core primitive is: a permissionless "top-up" entrypoint mutates shared per-target accounting state with no rate limit or minimum, which a griefer abuses cheaply to damage a value another party depends on. The local analog is `BandwidthManager.purchase()`: any caller, paying only the cheapest configured tier, can target an arbitrary `(chain, app)` bucket and push new `Subscription` rows onto `pallet-bandwidth`'s FIFO list [1](#0-0) . Once that bucket's list reaches the hard cap of 1024 entries, `push_subscription` silently evicts the oldest entry to make room for the new one [2](#0-1) , regardless of who paid for it or how many bytes it still holds.

### Finding Description
`purchase(app, tier, months, chain)` is a fully public function with no check that the caller is affiliated with `app` or `chain` — it only validates that `app`/`chain` are non-empty and `months > 0`, and that the tier price is configured [3](#0-2) . The `chain` argument is explicitly documented as unvalidated against the source chain, by design, to support cross-chain sponsorship [4](#0-3) .

On the Hyperbridge side, `on_accept` decodes the purchase message and calls `push_subscription(&msg.chain, &key, tier, bytes, duration)` keyed purely on the message body's `chain`/`app`, not on `request.source` [5](#0-4) . `push_subscription` enforces a hard cap of `MAX_SUBSCRIPTIONS = 1024` per `(chain, app)`; once at capacity, `list.remove(0)` evicts the oldest entry — win or lose, an event `SubscriptionEvicted` fires but the bytes are simply gone [2](#0-1) .

There is no per-buyer identity tracking, no minimum purchase size relative to existing entries, and no cooldown — an attacker can repeatedly call `purchase()` for the cheapest tier (`tierPrice[tier]` can be set arbitrarily low by governance and there's no floor enforced in code) against a victim's `(chain, app)` key until the FIFO list is saturated, then continue pushing to evict genuine, still-unconsumed, previously paid subscriptions belonging to unrelated legitimate buyers. This mirrors the report's exact broken invariant: a permissionless "deposit"-like call mutates state that another party's already-paid value depends on, with no floor/authorization gate to stop cheap griefing.

### Impact Explanation
This is a direct loss-of-funds path matching the bounty's "loss of funds" category: a legitimate buyer who pre-paid for a large/long-duration tier can have their still-unconsumed `remaining_bytes` permanently evicted and discarded by an unrelated, unprivileged attacker spending comparatively little on cheap-tier purchases against the same `(chain, app)` key. The evicted bytes are not refunded, transferred, or recoverable — the paying app simply loses the bandwidth it purchased. Because purchases queue rather than stack (documented behavior: "same-tier repurchases don't stack, they queue" [6](#0-5) ), a determined attacker only needs to fill the FIFO list up to 1024 entries once, then keep pace with legitimate purchases to keep evicting.

### Likelihood Explanation
The attack requires only an unprivileged caller with fee-token balance and no special role, relayer, prover, or governance access — `purchase()` is a normal external function reachable by anyone on any chain with a registered `BandwidthManager` [7](#0-6) . The only cost is the cheapest configured tier price times the number of purchases needed to reach/maintain the 1024-entry cap for the targeted `(chain, app)` key, which is bounded and attacker-controlled.

### Recommendation
Bind `purchase()` accounting to the payer in a way that prevents third parties from evicting subscriptions they didn't buy — e.g., track subscriptions per `(chain, app, payer)` instead of a single shared FIFO, or require `push_subscription` to evict only entries below a minimum remaining-value threshold, or reject/queue new purchases (instead of evicting) once the cap is reached until the oldest entry naturally expires. At minimum, emit-and-refuse (revert) rather than silently evict when the list is full, or raise the cap and add a cost floor proportional to what would be evicted.

### Proof of Concept
1. Governance configures `tierPrice[1]` to a low, non-zero price and `pallet-bandwidth::Tiers[TierOne]` to a small `(bytes, duration_secs)`.
2. Victim purchases `TierIndex::TierFour` for many months on `(chainX, victimApp)`, paying a large amount and expecting a long-lived, high-byte subscription (`push_subscription` appends it — `Allowance::<T>::mutate` at [8](#0-7) ).
3. Attacker repeatedly calls `BandwidthManager.purchase(victimApp, 1, 1, "chainX")` from any registered source chain, paying only the cheap `TierOne` price each time, until the `(chainX, victimApp)` list reaches 1024 entries.
4. Attacker continues calling `purchase()`; each new call causes `push_subscription` to evict the current oldest entry via `list.remove(0)` [8](#0-7)  — eventually reaching and evicting the victim's large, still-unconsumed `TierFour` subscription, which is lost permanently with only a `SubscriptionEvicted` event as evidence.
5. Victim's app now has less allowance than paid for; the attacker spent comparatively little relative to the victim's loss, and no relayer, prover, or admin participation was needed at any step.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L148-181)
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

**File:** modules/pallets/bandwidth/src/lib.rs (L467-486)
```rust
			let msg = PurchaseMessage::try_from(request.body.as_slice())?;
			let tier = TierIndex::try_from(msg.tier)
				.map_err(|_| anyhow::anyhow!(format!("unknown tier discriminant {}", msg.tier)))?;
			let cfg = Tiers::<T>::get(tier)
				.ok_or_else(|| anyhow::anyhow!(format!("tier {:?} is not configured", tier)))?;

			let bytes = cfg.bytes.saturating_mul(msg.months as u128);
			let duration = cfg.duration_secs.saturating_mul(msg.months as u64);

			let key = AppKey::truncate_from(msg.app);
			let expires_at = Self::push_subscription(&msg.chain, &key, tier, bytes, duration);

			Self::deposit_event(Event::BandwidthCredited {
				app_chain: msg.chain,
				app: key,
				paid_from: request.source,
				tier,
				bytes,
				expires_at,
			});
```

**File:** docs/content/developers/evm/bandwidth/purchasing.mdx (L192-205)
```text
## Sponsoring Another Chain

The `chain` argument is **not validated against the source chain** — see [Overview → Sponsorship](/developers/evm/bandwidth/overview#sponsorship) for the model. A buyer on Ethereum credits an app on Base by passing the credit chain id in `chain`:

```solidity
manager.purchase({
    app:    abi.encodePacked(appAddressOnBase),
    tier:   2,
    months: 6,
    chain:  bytes("EVM-8453")
});
```

The pallet keys allowance storage on `(msg.chain, msg.app)`, so the credit lands on Base regardless of which chain sent the payment. The recommended pattern for teams running a central treasury is to deploy `BandwidthManager` on one low-fee chain and sponsor bandwidth for app instances elsewhere.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L56-58)
```text
## Subscription Lifecycle

Each `(chain, app)` row holds a FIFO list of subscriptions, capped at **1024** entries. Every purchase appends a new subscription — same-tier repurchases don't stack, they queue.
```
