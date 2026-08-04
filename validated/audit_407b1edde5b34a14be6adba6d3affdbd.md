### Title
Unrefundable fee-token loss when `pallet-bandwidth`'s tier config is removed while a purchase message is in flight - ([File: evm/src/apps/BandwidthManager.sol], [File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`BandwidthManager.purchase()` pulls the buyer's fee-token payment immediately and dispatches a governance-priced credit message to `pallet-bandwidth` with `timeout: 0`. If the referenced tier is removed on the Hyperbridge side (`set_tier(tier, None)`) between the buyer's `purchase()` call and message delivery, `on_accept` deterministically rejects the message forever, but because the request was dispatched non-timeouting, the buyer's already-collected payment can never be refunded through any request/timeout code path — mirroring the external report's "resource removed mid two-phase flow, funds get stuck" bug class.

### Finding Description
`purchase()` first debits the buyer's fee token into the `BandwidthManager` contract, then dispatches a `BandwidthPurchaseMsg` with `timeout: 0, fee: 0`: [1](#0-0) 

On the Hyperbridge side, `on_accept` requires that the tier referenced in the message still be configured, else it errors out: [2](#0-1) 

Governance can remove a tier at any time via `set_tier(tier, None)`, which simply deletes the `TierConfig` entry: [3](#0-2) 

When the ISMP request handler invokes `on_accept` and it returns an error, the core protocol deletes the stored request receipt specifically "so it can be timed out": [4](#0-3) 

However, the purchase message was dispatched with `timeout: 0`. The module doc explicitly documents this as intentional: "Purchases dispatch with `timeout = 0`. If `on_timeout` ever fires it's an invariant violation, not a noop," and `on_timeout` unconditionally returns an error: [5](#0-4) 

This creates the exact broken invariant from the external report: a two-phase flow (pay-then-dispatch, then remote accept) where a governance-controlled resource (the tier) can be removed in the window between the two steps. Unlike a token removed from `acceptedTokens` in the Staking report — which at least becomes recoverable after an `unstakeDelay` — here the request is explicitly built to be non-timeouting, so there is no protocol-level path (neither successful delivery nor timeout) that returns the buyer's funds. The paid fee tokens sit commingled in the `BandwidthManager` contract's general balance with no per-purchase escrow accounting, recoverable only via a manual governance `Withdraw` action that isn't automatically tied to the specific failed purchase.

### Impact Explanation
This falls under "stealing or loss of funds" per the bounty scope: an unprivileged buyer's fee-token payment is unrecoverable through any protocol mechanism once the targeted tier is deleted mid-flight, because the message can neither be successfully processed (tier gone) nor timed out (dispatched with `timeout: 0`, and `on_timeout` treats any invocation as a bug and errors). The loss is deterministic and not contingent on a malicious relayer, prover, or admin colluding — it is a direct consequence of ordinary, permitted governance action (`set_tier(None)`) racing an ordinary user action (`purchase()`), matching the concurrency window flagged in the original Staking report.

### Likelihood Explanation
Medium. It requires no attacker at all — it's a race between a normal user's `purchase()` call and a normal governance `set_tier` update, both of which are legitimate, expected operations that occur in the protocol's documented lifecycle (tier repricing/deprecation). Given relayer delivery delays and challenge periods on Hyperbridge, the window during which an in-flight purchase can be invalidated by a tier removal is realistically sized (minutes to hours), so this is plausible in normal operation, not just a contrived edge case.

### Recommendation
- Do not delete `TierConfig` outright in `set_tier(None)`; instead mark it deprecated/frozen so in-flight purchases referencing it can still be credited (or explicitly refunded) rather than unconditionally rejected.
- Alternatively, dispatch bandwidth purchase messages with a non-zero timeout and implement a real `on_timeout` handler in `pallet-bandwidth` that dispatches a `Withdraw`-style refund message back to the source `BandwidthManager`, crediting the original payer rather than treating timeout as an unreachable invariant violation.
- If `on_accept` fails due to `UnknownTier`, `pallet-bandwidth` should synchronously dispatch a compensating refund/credit-reversal message to the `BandwidthManager` contract for the affected `payer`/`amount`, rather than silently dropping the request with no recourse.

### Proof of Concept
1. Governance configures `TierOne` via `pallet-bandwidth::set_tier(TierOne, Some(cfg))` and pushes prices to `BandwidthManager` on chain A via `dispatch_set_tiers`.
2. A buyer calls `BandwidthManager.purchase(app, TierOne, months, chain)` on chain A. The fee token is pulled from the buyer into the manager contract immediately, and a `BandwidthPurchaseMsg` is dispatched to Hyperbridge with `timeout: 0` (`evm/src/apps/BandwidthManager.sol:152-181`).
3. Before a relayer delivers the message, governance calls `pallet-bandwidth::set_tier(TierOne, None)`, deleting the `TierConfig` (`modules/pallets/bandwidth/src/lib.rs:274-291`).
4. The relayer delivers the purchase message; `on_accept` looks up `Tiers::<T>::get(tier)`, finds `None`, and returns an `Err` (`modules/pallets/bandwidth/src/lib.rs:454-489`).
5. The ISMP core handler deletes the request receipt so it "can be timed out" (`modules/ismp/core/src/handlers/request.rs:99-126`), but since the request was dispatched with `timeout: 0`, no timeout message will ever legitimately fire, and `pallet-bandwidth::on_timeout` is coded to treat any invocation as an invariant violation and error out (`modules/pallets/bandwidth/src/lib.rs:491-499`).
6. Result: the buyer's fee-token payment remains in `BandwidthManager`'s balance, the buyer receives no bandwidth credit, and there is no on-chain path back to the buyer — only a manual, out-of-band governance `Withdraw` action could recover the mixed funds, with no automatic accounting tying it to this specific failed purchase.

**Note on confidence**: I was unable to fully trace the exact semantics of `timeoutTimestamp == 0` at the lowest dispatcher/router level (whether the core protocol treats it as "never timeout" or "always immediately timeoutable") due to running out of search iterations before reading `modules/ismp/core/src/router.rs` and `modules/pallets/ismp/src/dispatcher.rs` in full. The pallet's own doc comment ("If `on_timeout` ever fires it's an invariant violation, not a noop") strongly supports the "never timeout" interpretation used above, but this specific point could not be independently confirmed against the low-level ISMP timeout-eligibility logic.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L152-181)
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

**File:** modules/pallets/bandwidth/src/lib.rs (L274-291)
```rust
		#[pallet::call_index(3)]
		#[pallet::weight(T::DbWeight::get().writes(1))]
		pub fn set_tier(
			origin: OriginFor<T>,
			tier: TierIndex,
			config: Option<TierConfig>,
		) -> DispatchResult {
			<T as pallet_ismp::Config>::AdminOrigin::ensure_origin(origin)?;
			match config {
				None => Tiers::<T>::remove(tier),
				Some(cfg) => {
					ensure!(cfg.bytes > 0 && cfg.duration_secs > 0, Error::<T>::InvalidTierConfig);
					Tiers::<T>::insert(tier, cfg);
				},
			}
			Self::deposit_event(Event::TierSet { tier, config });
			Ok(())
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

**File:** modules/pallets/bandwidth/src/lib.rs (L491-499)
```rust
		fn on_response(&self, _response: GetResponse) -> Result<Weight, anyhow::Error> {
			Err(ismp::Error::CannotHandleMessage.into())
		}

		/// Purchases dispatch with `timeout = 0`. If `on_timeout` ever
		/// fires it's an invariant violation, not a noop.
		fn on_timeout(&self, _timeout: Request) -> Result<Weight, anyhow::Error> {
			Err(anyhow::anyhow!("pallet-bandwidth purchases are non-timeouting"))
		}
```

**File:** modules/ismp/core/src/handlers/request.rs (L99-126)
```rust
		.map(|request| {
			let wrapped_req = Request::Post(request.clone());
			let mut lambda = || {
				let cb = router.module_for_id(request.to.clone())?;
				// Re-check the receipt right before dispatch. The up-front pass above
				// runs before any callback executes; a prior request's on_accept in
				// this same batch could have stored a receipt for this request
				// (directly or by re-entering the handler), and we must not invoke
				// on_accept a second time.
				if host.request_receipt(&wrapped_req).is_some() {
					Err(Error::DuplicateRequest { meta: wrapped_req.clone().into() })?
				}
				// Store request receipt to prevent reentrancy attack
				let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
				let res = cb.on_accept(request.clone()).map(|weight| {
					total_weights.saturating_accrue(weight);

					let commitment = hash_request::<H>(&wrapped_req);
					Event::PostRequestHandled(RequestResponseHandled {
						commitment,
						relayer: signer,
					})
				});
				// Delete receipt if module callback failed so it can be timed out
				if res.is_err() {
					host.delete_request_receipt(&wrapped_req)?;
				}
				Ok(res)
```
