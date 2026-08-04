## Analysis Summary

The `unstakeDelay` bug's core primitive is: **a permissionless, non-consensual operation performed by an attacker "on behalf of" a victim's account key corrupts or destroys state the victim already paid for, with no consent gate protecting the victim.** Hyperbridge's `pallet-bandwidth` has a structurally identical primitive in its purchase/eviction path.

### Title
Unauthenticated Bandwidth Purchase Sponsorship Allows Third-Party Eviction of a Victim App's Paid Allowance - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`BandwidthManager.purchase()` is fully permissionless: any caller, on any registered source chain, can credit bandwidth to **any** `(chain, app)` key of their choosing — the recipient app never consents and cannot block or filter incoming purchases. `pallet-bandwidth::on_accept` keys the FIFO subscription list purely by `msg.chain`/`msg.app` taken from the purchase payload, not by any relationship to the caller. `push_subscription` unconditionally evicts the oldest entry once the per-`(chain, app)` list reaches the hard cap of `MAX_SUBSCRIPTIONS = 1024`, destroying that entry's `remaining_bytes` regardless of size or how much runway it had left. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
The docs explicitly describe this as a designed "sponsorship" feature — anyone can top up bandwidth for any app on any chain, keyed by the message body's `chain`/`app` fields rather than `request.source`: [5](#0-4) 

That design choice means the pallet never checks that the purchaser has any relationship with, or consent from, the app being credited — much like the original bug where `stake()` could be called by any delegator on behalf of any beneficiary without the beneficiary's consent.

Each successful purchase appends a `Subscription` to a bounded FIFO list per `(app_chain, app)`. Once that list reaches 1024 entries, the *next* purchase for that same key evicts index 0 — the oldest surviving subscription — regardless of how many bytes it still has left: [6](#0-5) 

Because `purchase()` accepts an arbitrary attacker-chosen `app` and `chain` with no allowlist or consent check on the victim's side, an attacker can direct 1024 minimum-cost purchases (`tier=TierOne`, `months=1`) at a victim's `(chain, app)` key. This fills the FIFO list to capacity and the 1025th purchase evicts the victim's oldest live subscription — which could be a large, mostly-unconsumed allocation the victim legitimately purchased (e.g., a `TierFour` 8MB/month grant) — permanently destroying the bytes the victim already paid for.

This mirrors the reported bug's exploit shape exactly: a low-value, permissionless "deposit-like" call directed at someone else's key repeatedly corrupts state the victim relies on (there, the unstake timer; here, the FIFO queue position), with the attacker never needing the victim's cooperation or private key.

### Impact Explanation
The `SubscriptionEvicted` event is emitted, but this is an audit trail, not a mitigation — it does not restore the lost bytes or refund the victim. The victim's already-paid-for bandwidth (a bridge resource with real monetary value, since tiers cost real fee-token amounts) is unilaterally destroyed by an unrelated third party, and their app becomes unable to dispatch messages through Hyperbridge once its live subscriptions are exhausted (`GateError::NoAllowance` / `Insufficient`), effectively causing a denial-of-service on the app's cross-chain messaging until it repurchases. This matches the bounty's "loss of funds" and "logic attack" impact categories — bandwidth balances not moving exclusively to/staying with the rightful beneficiary. [7](#0-6) 

### Likelihood Explanation
The attack requires no privileged role, relayer collusion, or governance access — only fee tokens and the ability to call the public `purchase()` function 1024+ times targeting a chosen victim `app`/`chain`. Cost scales with the cheapest configured tier price × 1024, which is bounded by whatever governance sets for `TierOne`; there is no floor enforced on-chain preventing governance (or a future low-price tier) from making this cheap, and even at higher configured prices the attack is a fixed, one-time cost for permanent destruction of a competitor's/victim's paid allowance — an asymmetric griefing vector with no consent gate anywhere in the path.

### Recommendation
- Require the credited `app` to have opted in (e.g., an on-chain allow-sponsor flag per `(chain, app)`) before an unrelated payer's purchase can land, or
- Change eviction policy to never evict a subscription with `remaining_bytes` above some threshold / age below some threshold — e.g., only evict already-expired or fully-drained entries, growing the bound elastically instead of hard-evicting live value, or
- Track subscriptions per-payer instead of a single shared FIFO per `(chain, app)`, so a griefer's purchases cannot displace a legitimate holder's unconsumed allocation.

### Proof of Concept
1. Governance configures `TierOne` with some price `P` and victim `AppV` on `EVM-8453` purchases a large `TierFour` allocation (8MB, long duration) — 1 entry in `Allowance[EVM-8453][AppV]`.
2. Attacker calls `BandwidthManager.purchase(app=AppV_bytes, tier=1, months=1, chain="EVM-8453")` 1023 times from any chain with a registered manager, paying `1023 × P` in fee tokens — each call succeeds because `purchase()` performs no check that `msg.sender` relates to `AppV`. [1](#0-0) 
3. `Allowance[EVM-8453][AppV]` now holds 1024 entries (the victim's `TierFour` entry plus 1023 attacker-injected `TierOne` entries), all FIFO-ordered by `purchased_at`.
4. Attacker sends one more purchase (`1024th → triggers eviction`). `push_subscription` evicts index 0 — the victim's `TierFour` subscription if it was inserted first/is oldest — emitting `SubscriptionEvicted` with the victim's `remaining_bytes` as `lost_bytes`, permanently destroying that allocation. [8](#0-7) 
5. `AppV`'s remaining allowance is now whatever attacker-injected `TierOne` scraps survive, and `AppV` may hit `GateError::Insufficient`/`NoAllowance` on its next dispatch despite having paid for a much larger allocation moments earlier.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L148-163)
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

**File:** modules/pallets/bandwidth/src/lib.rs (L132-151)
```rust
	#[pallet::event]
	#[pallet::generate_deposit(pub(super) fn deposit_event)]
	pub enum Event<T: Config> {
		/// A `BandwidthManager` contract was bound to a source chain by
		/// admin. Future purchases from that chain are accepted only if
		/// `request.from` matches.
		ManagerRegistered { source: StateMachine, manager: H160 },
		/// A tier SKU was created, updated, or revoked (`config: None`).
		TierSet { tier: TierIndex, config: Option<TierConfig> },
		/// A new subscription was appended on the `(app_chain, app)`
		/// list as a result of a paid purchase from `paid_from`.
		BandwidthCredited {
			app_chain: StateMachine,
			app: AppKey,
			/// Chain that paid; differs from `app_chain` on sponsorship.
			paid_from: StateMachine,
			tier: TierIndex,
			bytes: BandwidthBytes,
			expires_at: u64,
		},
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

**File:** modules/pallets/bandwidth/src/types.rs (L19-22)
```rust
/// Hard cap on the subscription list per `(chain, app)`. Pushes
/// beyond this evict the oldest entry (FIFO).
pub const MAX_SUBSCRIPTIONS: u32 = 1024;
pub type MaxSubscriptions = ConstU32<MAX_SUBSCRIPTIONS>;
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
