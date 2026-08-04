## Analysis

The C4 report's core broken invariant: an **unprivileged actor can cheaply mutate a shared ordered structure keyed by a value the attacker controls**, forcing out (evicting) a legitimate, already-"paid-for" entry before it is consumed — causing the rightful party to lose value they already paid for, with no way to protect the entry once queued.

The closest concrete, locally-provable analog in this repo is `pallet-bandwidth`'s FIFO subscription ledger, keyed by `(app_chain, app)` — both fields taken directly from an attacker-supplied purchase message body, not authenticated against the caller.

### Title
Bandwidth FIFO subscription list can be griefed via forced eviction of a fully-funded, unconsumed subscription — ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth` stores prepaid bandwidth as a `BoundedVec<Subscription, 1024>` FIFO list per `(app_chain, app)` key [1](#0-0) . `push_subscription` evicts the list's *head* (`list.remove(0)`) whenever the list is at its 1024 cap — purely by FIFO position, with no check of the evicted entry's `remaining_bytes` or `expires_at` [2](#0-1) . Both `app_chain` and `app` are read straight from the attacker-controlled `PurchaseMessage` body during `on_accept`, and the "sponsorship" feature explicitly does **not** validate `chain` against `request.source` [3](#0-2) , and is documented as intentional: "The `chain` argument is not validated against the source chain" (docs/content/developers/evm/bandwidth/purchasing.mdx:192-205). Any address calling `BandwidthManager.purchase()` on any registered EVM source chain can therefore target an arbitrary victim `(app_chain, app)` pair.

### Finding Description
This mirrors the `CultureIndex` bug-class exactly: a FIFO/queue structure whose head-of-list eviction/selection logic is purely positional and never re-validates the value being displaced. In `CultureIndex`, the max-heap always surfaces the "top" entry for processing regardless of whether it can actually satisfy the required condition (quorum), letting an attacker occupy that slot and block everything behind it. In `pallet-bandwidth`, the FIFO list always evicts the *oldest* entry when full regardless of whether that entry still holds paid, unconsumed `remaining_bytes`, letting an attacker who repeatedly purchases the cheapest tier against a victim's `(app_chain, app)` key push the victim's legitimate, unexpired, un-drained subscription out of the list entirely.

Because `push_subscription` performs `list.remove(0)` unconditionally at the 1024 cap [4](#0-3) , a victim's subscription that still has most of its `remaining_bytes` unconsumed can be evicted purely by FIFO order — the same "selection ignores eligibility/state" flaw as `dropTopVotedPiece`'s quorum check being bypassable by heap position. The eviction is emitted as `SubscriptionEvicted { lost_bytes }` [5](#0-4) , explicitly acknowledging bytes the victim paid for are lost.

### Impact Explanation
`lost_bytes` represents real, already-paid value (the app bought a tier and dispatched real ERC-20 payment through `BandwidthManager.sol` before the credit message ever reaches the pallet). An attacker who forces eviction of a victim's active subscription causes that paid-for allowance to be destroyed before the victim's app can drain it via the gate — a direct loss-of-funds analog (loss of prepaid service value) rather than a purely off-chain/network DoS. Because `app_chain`/`app` are attacker-chosen from the purchase body and not authenticated against `request.source`, no privileged role, relayer, or governance compromise is required.

### Likelihood Explanation
The attack requires the attacker to out-purchase the victim to the point the FIFO list hits its 1024-entry cap on the same `(app_chain, app)` key, so cost scales with the cheapest configured tier price × up to 1024 purchases. This is exactly the same cost/likelihood profile the original C4 finding was judged on ("acknowledged... could happen naturally... in edge cases, not sure how to fix") — the docs for this pallet independently flag the eviction path as something that "in practice only happens under pathological repeat-buy behavior," i.e. the protocol authors are already aware this positional-eviction design has no re-validation guard, mirroring the acknowledged-but-unfixed status of the original report.

### Recommendation
When evicting at capacity, skip/refuse eviction of (or prefer evicting) entries with non-trivial `remaining_bytes` still unconsumed, or bound purchases per `(app_chain, app)` key to a rate/allowance that prevents FIFO-position griefing, similar in spirit to how `CultureIndex` was recommended to gain an admin path to remove/reprioritize a stuck entry.

### Proof of Concept
1. Victim purchases a large tier (e.g. TierFour, 8MB) for `(app_chain = EVM-8453, app = victimApp)`, appending subscription #1 to the list (`Allowance::<T>::mutate` push).
2. Attacker calls `BandwidthManager.purchase(app = victimApp, tier = TierOne, months = 1, chain = "EVM-8453")` 1023 times from any address on any registered source chain — none of these calls require any privilege; `chain`/`app` are taken verbatim from the message body [6](#0-5) .
3. On the 1024th attacker purchase, `push_subscription` detects the list is at cap and evicts index 0 — the victim's still-unconsumed TierFour subscription — emitting `SubscriptionEvicted { lost_bytes: <victim's near-full remaining_bytes> }` [7](#0-6) .
4. The victim's app now has to re-purchase bandwidth it already paid for; the attacker's cost was only 1024 × cheapest-tier price, none of which required compromising a relayer, prover, or admin key.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L105-118)
```rust
	/// Keyed by `app_chain` from the purchase message — *not*
	/// `request.source` — so a payer chain can sponsor an app that
	/// lives elsewhere. The inner `BoundedVec` holds subscriptions in
	/// chronological insertion order; the gate drains the front.
	#[pallet::storage]
	pub type Allowance<T: Config> = StorageDoubleMap<
		_,
		Twox64Concat,
		StateMachine,
		Blake2_128Concat,
		AppKey,
		SubscriptionList,
		ValueQuery,
	>;
```

**File:** modules/pallets/bandwidth/src/lib.rs (L168-175)
```rust
		/// The 1024-cap pushed out the oldest subscription. `lost_bytes`
		/// is what the user paid for and won't get to use.
		SubscriptionEvicted {
			app_chain: StateMachine,
			app: AppKey,
			tier: TierIndex,
			lost_bytes: BandwidthBytes,
		},
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

**File:** modules/pallets/bandwidth/src/lib.rs (L454-489)
```rust
	impl<T: Config> IsmpModule for Pallet<T> {
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
