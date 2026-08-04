## Analysis

I've confirmed the mechanism: bandwidth pricing is deliberately split across two independently-governed, uncoordinated storage locations — `Tiers` (bytes/duration, global, on Hyperbridge) and `tierPrice` (per-chain, on each `BandwidthManager`). This is structurally identical to the report's core defect: a config value (`erc20PaymentToken` / here, tier byte budget) can be changed by governance without the dependent, decimals/price-scaled value (`priceInWei` / here, `tierPrice`) being updated in lockstep, and nothing in the code enforces that they move together.

### Title
Tier byte-budget and per-chain tier price are updated by separate, uncoordinated governance calls, letting purchasers buy an enlarged allowance at a stale price - (File: modules/pallets/bandwidth/src/lib.rs, evm/src/apps/BandwidthManager.sol)

### Summary
`pallet-bandwidth::set_tier` changes a tier's `(bytes, duration_secs)` globally for every registered chain in one transaction, while `dispatch_set_tiers` must be called separately, per chain, to push the corresponding price to each `BandwidthManager` deployment. There is no atomic linkage, no on-chain ratio check, and no requirement that a `set_tier` byte-budget increase be paired with a price update before it takes effect — exactly the same class of gap the external report flags for `updatePaymentToken`, where changing one parameter silently invalidates dependent stored prices unless someone remembers to refresh them.

### Finding Description
`set_tier` writes the new `TierConfig{bytes, duration_secs}` directly into `Tiers<T>` with no dependency on price: [1](#0-0) 

The price side lives entirely on the EVM `BandwidthManager` and is only refreshed via a separate, per-chain `dispatch_set_tiers` call: [2](#0-1) 

`purchase()` on the manager charges strictly `tierPrice[tier] * months`, entirely blind to what `bytes`/`duration_secs` that tier currently represents on Hyperbridge: [3](#0-2) 

The pallet then credits bytes/duration using whatever `TierConfig` is live **at credit time**, not at purchase time, with no cross-check against what was paid: [4](#0-3) 

The documentation itself acknowledges this is a live risk that depends entirely on manual governance discipline, not an enforced invariant: [5](#0-4) [6](#0-5) 

Because `set_tier` (bytes/duration) is global and takes effect immediately for **every** registered `BandwidthManager`, while `dispatch_set_tiers` (price) must be individually re-sent to **each** chain's manager, any registered chain whose manager doesn't receive (or hasn't yet received) the follow-up `dispatch_set_tiers` call continues selling the *new, larger* byte/duration allowance at the *old, cheaper* `tierPrice`. Any unprivileged buyer on that chain can call `purchase()` at any time during this window and receive a disproportionately large allowance for a price set before the upgrade — permanently, since there is no expiry or reconciliation mechanism tying the two values together, and no revert path once the mismatched credit is minted on Hyperbridge.

### Impact Explanation
This is a direct value-extraction / fund-loss vector against the protocol treasury: purchasers pay the stale (lower) `tierPrice` but are credited the new (larger) `bytes`/`duration_secs`, systematically underpaying for bandwidth relative to governance's intended pricing. Because bandwidth purchases replace per-message protocol fees entirely (`allowance.cost = tier.price × months`), underpriced credits directly reduce protocol revenue without any compensating mechanism, and the mispriced allowance, once minted via `BandwidthCredited`, cannot be reversed.

### Likelihood Explanation
The multi-chain bandwidth deployment model (one `BandwidthManager` per source chain, `dispatch_set_tiers` required per chain) makes it structurally easy to miss a chain during a byte-budget change, and the codebase's own documentation flags this exact failure mode ("otherwise the same SKU costs different amounts depending on which chain a buyer is on"). No malicious relayer, prover, or leaked key is needed — the exploit is executed entirely through the public, unprivileged `purchase()` entrypoint by any buyer once a legitimate governance `set_tier` call outruns the corresponding `dispatch_set_tiers` push to one or more managers.

### Recommendation
Tie tier byte/duration changes to price atomically: either (a) require `set_tier` to also carry/validate a price-per-byte ratio and reject purchases whose price-to-bytes ratio deviates beyond a configured tolerance, or (b) version tiers so that a `set_tier` config change mints a new tier discriminant instead of mutating the existing one in place, forcing managers to explicitly opt into the new SKU with a fresh price before it can be purchased, preventing any window where enlarged allowances are purchasable at old prices.

### Proof of Concept
1. Governance calls `pallet_bandwidth::set_tier(TierOne, Some({bytes: 10_000_000 → 100_000_000, duration_secs: unchanged}))`, effective immediately and globally for all registered chains. [7](#0-6) 
2. Governance intends to also raise `tierPrice[TierOne]` via `dispatch_set_tiers` on every chain, but this is a second, independent, per-chain transaction; suppose it lands on chain A but is delayed/omitted for chain B. [2](#0-1) 
3. On chain B, any address calls `BandwidthManager.purchase(app, TierOne, 1, chainB)`; the contract only reads the still-stale `tierPrice[TierOne]` and charges accordingly. [8](#0-7) 
4. The purchase message reaches `pallet-bandwidth`'s `on_accept`, which reads the **current** (already-upgraded) `TierConfig` for `TierOne` and credits `bytes × months` using the new 100,000,000-byte budget, per the documented flow. [4](#0-3) 
5. The buyer has now paid the old 10 MB price for a 100 MB allowance, and the credited `Subscription` is immutable and irreversible.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L270-291)
```rust

		/// Pass `config: None` to revoke. Non-zero `bytes` requires a
		/// non-zero `duration_secs` so a purchase can't expire on
		/// creation.
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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L121-134)
```text

1. **Deploy.** `BandwidthManager(owner)` is deployed on the source chain.
2. **Bind host.** Owner calls `setHost(hostAddr)` — one-shot.
3. **Register.** Governance calls `pallet-bandwidth::set_manager(source, manager_addr)` on Hyperbridge.
4. **Configure tiers.** Governance sets `(bytes, duration_secs)` on the pallet via `set_tier`, then pushes the price side to the manager via `dispatch_set_tiers`.

Until every step lands, purchases fail — usually `UnknownManager` (pallet) or `UnknownTier()` (manager).

**Purchase (per top-up):**

5. **Approve.** Buyer approves the manager for `tier.price × months` scaled to the local fee token's decimals.
6. **Call `purchase()`.** Manager pulls the fee token, encodes a `BandwidthPurchaseMsg { app, tier, months, chain }`, and dispatches an ISMP POST to `pallet-bandwidth` (recipient `"BWMARKET"`) with `timeout: 0` and `fee: 0`. Emits `BandwidthPurchased` with the dispatch commitment.
7. **Deliver.** A relayer carries the message to Hyperbridge.
8. **Credit.** Pallet's `on_accept` checks `request.from` matches the registered manager, decodes the body, looks up `TierConfig`, computes `bytes × months` and `duration_secs × months`, and appends a fresh `Subscription` to the `(app_chain, app)` FIFO list. Emits `BandwidthCredited { app_chain, app, paid_from, tier, bytes, expires_at }`. If the list was at the 1024 cap, the oldest entry is evicted with `SubscriptionEvicted`.
```

**File:** docs/content/developers/evm/bandwidth/configuration.mdx (L99-107)
```text
This dispatches a `SetTiers` message to the registered manager on `target`. The manager's `onAccept` writes `tierPrice[tier] = price18d` for each row and emits `TierSet(tier, price18d)`. Tiers not in the batch are left untouched.

Prices are 18-decimal regardless of the local fee token's decimals — the contract scales at purchase time. See [Purchasing → Decimal scaling](/developers/evm/bandwidth/purchasing#decimal-scaling) for the rules and the `PriceNotRepresentable()` failure mode.

Repeat for every chain that should sell the same tiers — tier prices are per-chain because the manager contract is per-chain.

<Callout type="warn" title="Keep prices in sync">
If you change a tier's price, dispatch the update to every chain where a manager is deployed — otherwise the same SKU costs different amounts depending on which chain a buyer is on. The pallet emits `TiersDispatched { target, count, commitment }` for each push so you can audit.
</Callout>
```

**File:** docs/content/developers/evm/bandwidth/governance.mdx (L25-28)
```text
| `set_manager(source, manager)` | Register or overwrite the `BandwidthManager` authorised to send purchases from `source`. | `ManagerRegistered` |
| `set_tier(tier, config)` | Create, update, or revoke (`config: None`) a tier's `(bytes, duration_secs)`. Pallet-side only. | `TierSet` |
| `dispatch_set_tiers(target, updates)` | Push a `SetTiers` message to the `BandwidthManager` on `target`. Updates EVM-side prices. | `TiersDispatched` |
| `dispatch_withdraw(target, token, beneficiary, amount)` | Push a `Withdraw` message to the `BandwidthManager` on `target`. Drains the manager's treasury. | `WithdrawalDispatched` |
```
