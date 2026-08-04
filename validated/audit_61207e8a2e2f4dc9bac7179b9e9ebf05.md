## Finding

### Title
Bandwidth purchase pricing has no cross-chain value normalization between fee tokens, letting an attacker buy bytes on the cheapest fee-token chain and credit any app on any chain at the intended (higher) rate - ([File: evm/src/apps/BandwidthManager.sol], [File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth` and `BandwidthManager.sol` split the tier "SKU" across two sides: the pallet owns `(bytes, duration_secs)` globally, and each per-chain `BandwidthManager` deployment owns a nominal 18-decimal `price18d` for the same tier discriminant. The purchase flow only normalizes for the fee token's *decimal count*, never for its *economic value*. Because a single tier's byte/duration credit is identical no matter which chain (and which fee token) paid for it, and because the purchase message explicitly lets a payer on one chain credit an app on any other chain ("sponsorship"), an attacker can pay in whichever manager's fee token is worth the least and receive the exact same byte allowance as if they had paid with a fully-valued token elsewhere. This is the same broken invariant as the GMX virtual-balance bug: a fungible, shared accounting unit (bytes) is credited from heterogeneous value sources without any FX/oracle conversion between them.

### Finding Description
`purchase()` in `evm/src/apps/BandwidthManager.sol` computes the amount to pull purely from the nominal price and the local fee token's decimals: [1](#0-0) 

`price18d` is set by governance via `dispatch_set_tiers`/`SetTiers`, and is intended to represent a dollar-equivalent value, but nothing on-chain ties it to an oracle price of `feeToken()` — it's an arbitrary 18-decimal number, pushed independently to each `BandwidthManager` deployment on each source chain: [2](#0-1) 

On the Hyperbridge side, `on_accept` credits bytes purely from the pallet-global `TierConfig` (`bytes`, `duration_secs`), scaled only by `months` — it never looks at what fee token, what price, or what chain paid: [3](#0-2) 

Crucially, the credited bucket key is `msg.chain` (the app's chain) from the purchase body, not `request.source` (the chain that paid) — this is the documented "sponsorship" feature, letting a payer on chain A credit an app on chain B: [4](#0-3) 

So the corrupted value is the credited `bytes`/`duration` in `Allowance::<T>` (and by extension every `BandwidthConsumed` check against it) — it is treated as a fungible, chain/token-agnostic quantity, but it was actually purchased with tokens of divergent real value across chains, with zero price-oracle reconciliation between managers, mirroring `MarketUtils.applyDeltaToVirtualInventoryForSwaps` storing a raw token-amount delta as if it were already denominated in the virtual unit.

### Impact Explanation
Every `BandwidthManager` deployment is configured with its own `feeToken()` (via the local host) and its own nominal `price18d` per tier, set independently by governance dispatches. If any two managers' fee tokens diverge in real value — a stablecoin depeg, a low-liquidity/thin wrapped asset, or simply governance setting a slightly-too-low nominal price on one chain relative to another — a buyer pays real value `X` on the cheap chain and receives byte/duration credit worth real value `Y > X` when priced against the "correct" chain's fee token. Because the credit lands in a shared, chain-agnostic ledger keyed only by `(app_chain, app)` and is fungible across the network's bandwidth gate (any ISMP dispatch draws down the same bucket regardless of which manager funded it), this directly undercuts the protocol's intended bandwidth revenue and lets any unprivileged buyer systematically arbitrage the price divergence — exactly the "unfairly draining any impact discounts available to legitimate traders" pattern from the seed report, translated to bandwidth economics: draining intended bandwidth revenue/allowance value from the protocol via a permissionless, unprivileged entrypoint.

### Likelihood Explanation
No admin, relayer, or prover collusion is required — only the ordinary multi-chain deployment topology (different fee tokens/managers per chain) plus the permissionless `purchase()` call. Any nominal price divergence between managers (which can arise from a legitimate stablecoin depeg without any governance error) is immediately and directly exploitable through the public `purchase()` function, with no additional trust assumptions.

### Recommendation
Either (a) tie `price18d` to an oracle-derived USD value of each manager's `feeToken()` at purchase time and recompute the local price on every governance push so nominal prices track real value, or (b) key `Allowance` and the byte/duration credit by the fee token/chain that actually paid, so cross-chain sponsorship cannot arbitrage a locally underpriced fee token into full-value bytes elsewhere. At minimum, gate cross-chain sponsorship credits behind a price-equivalence check comparable to the recommendation in the seed report ("use oracle prices and convert the collateral token to the specific virtual token").

### Proof of Concept
1. Governance registers two `BandwidthManager` deployments, one on Chain A with `feeToken = USDC` and one on Chain B with `feeToken = USDT`, both configured via `dispatch_set_tiers` with the same nominal `price18d` for `TierOne`.
2. USDT depegs to $0.85 (or Chain B's manager is simply mispriced relative to Chain A by any amount) while `price18d` on both managers stays unchanged.
3. Attacker calls `purchase(app, TierOne, months, chain=B)` on Chain A's manager... no — attacker calls `purchase()` on Chain B's manager (paying in the cheap USDT), setting the purchase message's `chain` field to the AppKey/chain the attacker actually wants credited (any chain, per the sponsorship design in `on_accept`).
4. `pallet-bandwidth::on_accept` on Hyperbridge credits `TierOne`'s full `bytes`/`duration_secs` to `(chain, app)` exactly as if paid at full value — see the credit logic at [3](#0-2) .
5. The attacker has obtained bandwidth allowance for ~15% less real value than intended, and can repeat this indefinitely as long as the price divergence persists, draining the protocol's intended bandwidth-sale value with a purely permissionless call.

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

**File:** modules/pallets/bandwidth/src/lib.rs (L293-322)
```rust
		/// Push tier prices to a remote `BandwidthManager` (the EVM
		/// side holds prices; this pallet holds bytes/duration).
		/// `updates` is `(tier, price18d)` pairs.
		#[pallet::call_index(4)]
		#[pallet::weight(T::DbWeight::get().writes(1))]
		pub fn dispatch_set_tiers(
			origin: OriginFor<T>,
			target: StateMachine,
			updates: Vec<(TierIndex, U256)>,
		) -> DispatchResult {
			<T as pallet_ismp::Config>::AdminOrigin::ensure_origin(origin)?;
			ensure!(!updates.is_empty(), Error::<T>::EmptyTierBatch);
			let manager = BandwidthManager::<T>::get(&target).ok_or(Error::<T>::UnknownManager)?;

			let count = updates.len() as u32;
			let rows: Vec<Tier> = updates
				.into_iter()
				.map(|(t, p)| {
					let id_u32: u32 = t.into();
					Tier { tier: to_alloy_u256(U256::from(id_u32)), price: to_alloy_u256(p) }
				})
				.collect();

			let mut body = vec![ACTION_SET_TIERS];
			body.extend(rows.abi_encode_params());

			let commitment = Self::dispatch_governance(target, manager, body)?;
			Self::deposit_event(Event::TiersDispatched { target, count, commitment });
			Ok(())
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
