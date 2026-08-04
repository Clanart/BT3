### Title
Unbounded, permissionless `purchase()` on `pallet-bandwidth`/`BandwidthManager` lets an attacker evict other apps' already-paid bandwidth subscriptions - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth`'s `Allowance` list for each `(app_chain, app)` pair is a FIFO `BoundedVec` capped at `MAX_SUBSCRIPTIONS` (1024). Any address on any registered source chain can call `BandwidthManager.purchase()` and, via `on_accept`, push a new `Subscription` onto *any* `(chain, app)` bucket by simply naming the target `app`/`chain` in the message body — there is no relationship required between the payer and the app being credited (this is the documented "sponsorship" feature). When the target list is already at the 1024 cap, `push_subscription` silently evicts the oldest (head) entry to make room. This mirrors the EigenPod bug's root cause: a partially-consumed resource (validator ETH / prepaid bandwidth) that is protected by a hard boundary condition (the min-scrape threshold / the 1024-slot cap) can be pushed into an unrecoverable state by an ordinary, unprivileged actor's transaction, and the only way to make the victim whole is an out-of-band admin action (`force_credit`) that was never intended as a routine remedy.

### Finding Description
`push_subscription` in `modules/pallets/bandwidth/src/lib.rs` evicts the oldest live subscription whenever a purchase lands on a full `(app_chain, app)` list: [1](#0-0) 

The purchase entry point (`on_accept`) only checks that the *sender contract* on the source chain matches the registered `BandwidthManager` for that source — it does **not** check that the caller (`msg.sender` on the EVM side) has any relationship to the `app`/`chain` being credited: [2](#0-1) 

On the EVM side, `purchase()` is fully public and takes `app`/`chain` as arbitrary caller-supplied parameters: [3](#0-2) 

This is the intended "sponsorship" design — a payer on one chain can top up any app on any chain — but it also means **anyone can push cheap, minimal-tier subscriptions onto a victim app's queue an unbounded number of times**. Once the victim's `(app_chain, app)` list reaches 1024 entries, every further purchase (attacker's or a legitimate buyer's) evicts the oldest entry and emits `SubscriptionEvicted`, permanently destroying the un-consumed, already-paid-for byte allowance of whichever subscription was at the head — which could belong to the legitimate app owner, not the attacker.

This directly parallels the EigenPod report's broken invariant: a resource is committed/paid-for, a structural limit (min-scrape threshold / FIFO cap) exists to bound normal operation, and an ordinary transaction from an unprivileged actor can push the system past a boundary that destroys value with no automatic recovery — recovery requires an admin ('reactivate validators' / `force_credit`) which was designed for migrations/refunds, not as a remedy for adversarial eviction.

### Impact Explanation
Bandwidth subscriptions represent real money paid via `purchase()` (tier price × months, pulled in the host's fee token). An attacker who can cheaply push 1024 filler purchases onto a target `(chain, app)` bucket can force-evict a victim's legitimately purchased, unconsumed bandwidth allowance — a direct loss of prepaid funds/service for the victim app, with the queue mutation triggered entirely by the attacker's own unprivileged transactions. Because the gate (`try_consume`) enforces hard rejection once allowance is exhausted, the victim app's cross-chain messages could then start failing (`GateError::NoAllowance`), which is itself a denial-of-service on legitimate protocol traffic caused by a logic flaw in the accounting structure, not merely network-level congestion. This fits the "logic attacks" / "loss of funds" categories in the bounty scope, mirroring the "medium" severity of the EigenPod original (recoverable only via an unintended admin action, `force_credit`, analogous to reactivating validators).

### Likelihood Explanation
Likelihood is a function of tier pricing set by governance: `set_tier`/`dispatch_set_tiers` control the price per tier per month, and the pallet places no minimum price floor nor per-source/per-app rate limiting on purchases. If any tier is priced low relative to the target list length, or if the attacker only needs to evict a handful of entries near the front of a shallow queue (not necessarily fill all 1024 slots — eviction only occurs once the specific list is full, but a popular/heavily-purchased app could reach that state faster, or governance could configure very cheap tiers for bootstrapping), the attack becomes economically feasible. It requires no relayer collusion, no compromised keys, and no privileged role — only calling `purchase()` repeatedly, which is exactly the kind of unprivileged, public-entrypoint path the bounty prioritizes.

### Recommendation
- Bind subscription slots to `(app_chain, app, payer)` or otherwise prevent one payer's purchases from displacing another payer's unexpired, unconsumed subscription belonging to a different original buyer.
- Alternatively, replace hard FIFO eviction with a rejection (`Err`) when the list is full, requiring governance/administrative capacity expansion (`force_credit`/`set_tier`) rather than silently destroying value, or refund/re-route the evicted amount instead of deleting it.
- Track "already consumed" vs "not yet consumed" state separately so eviction only ever targets subscriptions whose `remaining_bytes` are already fully drained, never economically active/unspent allowances.
- Add an event-driven alert plus per-`(source, app)` purchase-rate limiting to prevent bulk cheap-tier flooding.

### Proof of Concept
1. Governance sets a cheap `TierOne` price (e.g., minimal USD-equivalent) via `set_tier`/`dispatch_set_tiers`.
2. Victim `AppX` on `EVM-8453` legitimately purchases and holds N < 1024 live subscriptions with substantial unconsumed `remaining_bytes`.
3. Attacker repeatedly calls `BandwidthManager.purchase(app = AppX, tier = TierOne, months = 1, chain = "EVM-8453")` from any wallet, on any registered source chain, `1024 - N` times to fill the queue, then continues purchasing.
4. Each purchase beyond the cap triggers `push_subscription`'s eviction path: [4](#0-3) 
5. `SubscriptionEvicted` fires for AppX's oldest legitimate subscription, permanently destroying its `lost_bytes` allowance though it was never consumed by the gate.
6. AppX's dispatches subsequently begin failing with `GateError::NoAllowance`/`Insufficient` once enough of its legitimate paid allowance has been evicted, even though AppX paid for and expected that bandwidth to remain until its `expires_at`.

**Uncertainty note:** I was unable to determine the exact configured tier prices in production (they are set by governance via `set_tier`/`dispatch_set_tiers` at runtime and are not hardcoded in the repo), so the real-world cost of filling/evicting a specific app's queue cannot be quantified from the code alone — this affects the practical likelihood estimate above.

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L455-489)
```rust
		fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
			let manager = BandwidthManager::<T>::get(&request.source).ok_or_else(|| {
				anyhow::anyhow!(format!("no bandwidth manager registered for {:?}", request.source))
			})?;

			if request.from != manager.0.to_vec() {
				return Err(anyhow::anyhow!(format!(
					"purchase from unauthorised sender on {:?}: expected {:x?}, got {:x?}",
					request.source, manager.0, request.from
				)));
			}

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

			Ok(Weight::zero())
		}
```

**File:** evm/src/apps/BandwidthManager.sol (L148-182)
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
