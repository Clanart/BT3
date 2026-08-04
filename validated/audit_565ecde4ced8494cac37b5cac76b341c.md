### Title
Unprivileged Bandwidth-Queue Griefing Evicts a Victim's Paid Subscription Before It Drains - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth` keeps a single shared, per-`(app_chain, app)` FIFO queue of purchased bandwidth subscriptions, capped at `MAX_SUBSCRIPTIONS = 1024`. Because the queue is keyed only by `(app_chain, app)` and not by payer, and because "any deployment can sponsor any app on any chain," any unprivileged caller who can reach a registered `BandwidthManager.purchase()` on any source chain can push cheap, tiny subscriptions into a victim's bucket until the FIFO cap is hit, forcibly evicting the victim's real, still-unspent (already-paid-for) subscription. This is the same broken-invariant class as the reported PoolCommitter bug: an unprivileged actor performs an ordinary, permitted action (uncommit / purchase) that, combined with the queue's blind head-eviction logic, destroys state that a third party is relying on — except here the destroyed state is directly redeemable value (paid-for bandwidth bytes) rather than a commit slot.

### Finding Description
The subscription ledger is a bounded FIFO per `(app_chain, app)`: [1](#0-0) 

Every purchase — regardless of payer chain — appends unconditionally to the same list and evicts the oldest entry once the list is full: [2](#0-1) 

The purchase entrypoint (`on_accept` from any registered `BandwidthManager` on any source chain) does not check who bought the app's *existing* subscriptions, does not rate-limit the number of purchases per caller, and does not weight eviction by remaining value — it is a pure oldest-first pop: [3](#0-2) 

Documentation confirms the sponsorship model is intentional and cross-payer: "any deployment can sponsor any app on any chain," and the eviction is explicitly oldest-first with no value weighting: [4](#0-3) 

Because the eviction key is `(app_chain, app)` and not `(payer, app_chain, app)`, an attacker who is not the victim and did not pay for the victim's subscription can still force it out of the queue: they only need to make 1024 cheap purchases (any tier, any months, from any chain) targeting the same `app_chain`/`app` pair the victim funded. `push_subscription`'s `list.remove(0)` has no concept of "this entry still has a lot of value left" — it always kills the head regardless of `remaining_bytes` or how much was paid for it.

This mirrors the reported bug's structure precisely: a normal, permissionless action (`_uncommit`/`purchase`) interacting with FIFO/queue-pointer bookkeeping that a hostile actor can trigger to erase state belonging to someone else, at a cost to the attacker much lower than the value destroyed for the victim (especially when the victim bought a large, long-duration tier and the attacker buys 1024 cheap, single-month, minimum-tier subscriptions).

### Impact Explanation
This is a direct loss-of-funds vector: the victim paid real fee-tokens to `BandwidthManager.purchase()` for a byte allowance with a fixed expiry, and an unrelated unprivileged actor can force that unspent, already-paid allowance out of the ledger before the gate ever drains it. `SubscriptionEvicted` is emitted, but the victim has no recourse — the bytes are gone and the fee already collected in the manager contract is not refunded. This falls squarely under "stealing or loss of funds" / "logic attacks," matching the impact-gate requirements, since it requires no malicious relayer, admin, or prover — only a public, permissionless `purchase()` call reachable by anyone.

### Likelihood Explanation
Reaching the vulnerable path requires no privileges: `purchase()` on `BandwidthManager.sol` is public and permissionless, and the pallet's `on_accept` only checks that `request.from` equals the registered manager address for the source chain — it never restricts who the manager relays purchases for. The only cost to the attacker is paying for 1024 cheap purchases (the manager's cheapest configured tier, `months = 1`), which can be substantially less than the value of a victim's high-tier, multi-month purchase they are trying to evict. This makes the attack economically attractive whenever tier pricing is not roughly linear/proportional to eviction cost, which governance does not enforce anywhere in the reviewed code.

### Recommendation
Do not evict purely on FIFO position irrespective of remaining value or payer. Options: (1) key/weight eviction by remaining `bytes` so higher-value subscriptions cannot be trivially evicted by many minimal ones, (2) impose a minimum eviction cost floor (e.g. reject or refund-fail purchases that would evict a subscription whose paid value exceeds the incoming purchase's value), or (3) separate the "who is allowed to push into this bucket" from "who is allowed to evict its contents," e.g. rate-limit or fee-scale insertions per source chain/payer so eviction cost scales with the value destroyed rather than a fixed queue slot.

### Proof of Concept
1. Victim (attacker's target app on `app_chain = Evm(A)`) legitimately buys the largest tier for many months via `BandwidthManager.purchase()` on some source chain `S1`, producing a `Subscription` with large `remaining_bytes` and long `expires_at`, appended to `Allowance[(A, app)]` (see `on_accept`, `push_subscription`).
2. Attacker, from any registered source chain (can be the same chain `S1` or a different registered manager `S2` — sponsorship is cross-chain by design), calls `purchase()` 1024 times with `tier = TierOne`, `months = 1` (the cheapest configuration), each targeting the same `chain = A`, `app = app` pair.
3. Each purchase calls `push_subscription`, which appends to `Allowance[(A, app)]`; once the list reaches `MAX_SUBSCRIPTIONS = 1024`, `list.remove(0)` starts evicting the oldest entry — the victim's large paid subscription is evicted, along with `SubscriptionEvicted` emitted showing the lost bytes.
4. The victim's app now has 0 or near-0 remaining bandwidth despite having paid for a long-duration, high-byte allowance, and cannot recover the spent fee-tokens already collected by the manager contract.

Note: I was unable to fully verify whether any additional per-payer rate limiting exists elsewhere in the runtime configuration (outside the pallet/contract code reviewed) that might mitigate this; the pallet and contract code themselves impose none.

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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L56-77)
```text
## Subscription Lifecycle

Each `(chain, app)` row holds a FIFO list of subscriptions, capped at **1024** entries. Every purchase appends a new subscription — same-tier repurchases don't stack, they queue.

A subscription is immutable across its lifetime:

| Field             | Behavior                                                                       |
| ----------------- | ------------------------------------------------------------------------------ |
| `tier`            | Recorded at purchase time. Used for events and analytics, not for gating.      |
| `remaining_bytes` | Drains as the gate consumes messages. Pops once it hits zero.                  |
| `expires_at`      | Fixed at purchase. Never extends — a repurchase is a _new_ row, not a renewal. |
| `purchased_at`    | Insertion timestamp. Fixes FIFO order under same-block buys.                   |

### Drain order

The gate drains from the **head** of the list. Once the head is fully consumed it pops and continues into the next. If the next entry is expired it's swept silently — what you paid for is yours only until it expires.

This matters when you queue multiple tiers: the cheapest/oldest entry is consumed first regardless of which tier it came from. Plan top-ups so a higher tier doesn't sit behind a soon-to-expire lower tier you'd rather burn last.

### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```
