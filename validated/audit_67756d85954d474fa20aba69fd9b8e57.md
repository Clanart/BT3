### Title
Stale decimal-precision invariant lets asset decimal drift inflate cross-chain transfer amounts in `pallet-hyper-fungible-token` - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`, `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`pallet-hyper-fungible-token` only enforces `erc_decimals >= local_decimals` once, at `register_token`/`update_token` time. `send()`, `on_accept()`, and `on_timeout()` all re-derive `local_decimals` **dynamically** at call time via `fungibles::metadata::Inspect::decimals()`, rather than caching the value that was validated at registration. If a non-native asset's decimals are later increased (a normal, permissionless `pallet-assets::set_metadata` call by the asset's owner — not governance, not a relayer/prover), the invariant checked at registration silently breaks, and the decimal-scaling arithmetic in `convert_to_erc20`/`convert_to_balance` collapses to a no-op scale (`saturating_sub` clamps to 0), producing a cross-chain amount that no longer reflects the value actually locked or burned.

### Finding Description
`register_token` and `update_token` validate the invariant once: [1](#0-0) 

But the scaling math in `send()` fetches `local_decimals` live, not the value checked at registration: [2](#0-1) 

`convert_to_erc20`/`convert_to_balance` compute the scale exponent with `saturating_sub`, which silently becomes a no-op (exponent 0) the moment `local_decimals > erc_decimals`, instead of erroring or dividing the other way: [3](#0-2) 

Because `<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(asset_id)` is not the value pinned at `register_token` time but whatever `pallet-assets` reports right now, any actor able to call `pallet_assets::set_metadata` on that asset (typically the asset's `Owner`, which in a permissionless asset-creation runtime is an ordinary user, not governance) can raise the asset's local decimals after registration and permanently break the once-validated `erc_decimals >= local_decimals` guarantee — without the pallet ever re-checking it. The same stale-decimals read pattern recurs in the inbound path: [4](#0-3) [5](#0-4) 

### Impact Explanation
Once `local_decimals > erc_decimals`, `convert_to_erc20` in `send()` stops scaling the amount up to the destination's higher/expected precision and instead forwards the raw local-unit integer unscaled. Since the destination interprets that integer at the (lower) EVM `erc_decimals`, the effective minted/released value on the destination is inflated by `10^(local_decimals - erc_decimals)` relative to what was actually escrowed or burned on the source chain — a false amount is accepted and executed on the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract, i.e. unauthorized fund creation for the attacker's own transfer. This is a direct violation of the bridge custody invariant ("bridged assets... must move exactly once and only to the rightful beneficiary and amount") triggerable purely by manipulating asset metadata the pallet trusts but never re-validates.

### Likelihood Explanation
Requires: (1) an asset registered non-natively through `register_token`/`update_token` (a normal integration path, not privileged from the attacker's perspective since the attacker is the asset's `Owner`, not the pallet's `CreateOrigin`), and (2) the ability to call `pallet_assets::set_metadata` on that asset — a standard, un-gated capability of the asset `Owner` in most `pallet-assets` deployments, especially where asset creation is permissionless. No relayer, prover, or governance collusion is needed; the drift is purely a local, on-chain state change by the asset owner that the bridging pallet fails to re-check before trusting `Assets::decimals()` for scaling.

### Recommendation
Cache the `local_decimals` value validated at `register_token`/`update_token` time in a dedicated storage item (rather than re-deriving it from `Assets::decimals()` at transfer time), or re-validate `erc_decimals >= local_decimals` on every `send()`/`on_accept()`/`on_timeout()` call and reject the transfer/message if the invariant no longer holds. Alternatively, disallow decimal changes on any asset registered with the pallet, or fail closed (return an error) instead of using `saturating_sub`, which silently converts an invalid decimals relationship into a no-op scale factor.

### Proof of Concept
1. Governance registers asset `X` (non-native, `is_native=false`) via `register_token` with `local_decimals = 6` and `erc_decimals = 18` for `StateMachine::Evm(dest)`; the `ensure!(config.decimals >= local_decimals, ...)` check passes.
2. The asset's `Owner` (an ordinary account, not the pallet's `CreateOrigin`) calls `pallet_assets::set_metadata` on asset `X` and raises its `decimals` field to `20`.
3. The owner calls `pallet_hyper_fungible_token::send()` with `asset_id = X`, burning `amount` units of the asset at the new 20-decimal precision.
4. Inside `send()`, `decimals = Assets::decimals(X) = 20`, `erc_decimals = Precisions::get(X, dest) = 18`. `convert_to_erc20(amount, 18, 20)` computes `18u8.saturating_sub(20u8) = 0`, so `erc20_amount = amount * 10^0 = amount` — unscaled.
5. The destination `HyperFungibleToken` contract receives `erc20_amount = amount` and mints/releases it at its native 18-decimal precision, crediting the beneficiary `10^2` (or generally `10^(local_decimals - erc_decimals)`) times more value than the `amount` actually burned on the source chain represented at the correct exchange rate — an unauthorized value inflation across the bridge triggered entirely by the attacker's own permissionless asset-metadata change.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L286-295)
```rust
				}
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					params.asset_id.clone(),
				)
			};

			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L352-355)
```rust
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L39-59)
```rust
/// Converts an ERC20 U256 amount to a local balance type
///
/// Divides by 10^(erc_decimals - local_decimals) to scale down from ERC20 precision.
/// The target type must implement `FromStr`.
pub fn convert_to_balance<B: core::str::FromStr>(
	value: U256,
	erc_decimals: u8,
	local_decimals: u8,
) -> Result<B, B::Err> {
	let dec_str = (value /
		U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32)))
	.to_string();
	dec_str.parse::<B>()
}

/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L74-91)
```rust
		// Convert amount from ERC20 denomination to local
		let decimals = if local_asset_id == T::NativeAssetId::get() {
			T::Decimals::get()
		} else {
			<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
				local_asset_id.clone(),
			)
		};
		let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), source)
			.ok_or(HftError::DecimalsNotConfigured(source))?;
		let amount = convert_to_balance::<
			<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
		>(
			U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
			erc_decimals,
			decimals,
		)
		.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L239-255)
```rust
				let decimals = if local_asset_id == T::NativeAssetId::get() {
					T::Decimals::get()
				} else {
					<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
						local_asset_id.clone(),
					)
				};
				let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), dest)
					.ok_or(HftError::DecimalsNotConfigured(dest))?;
				let amount = convert_to_balance::<
					<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
				>(
					U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
					erc_decimals,
					decimals,
				)
				.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;
```
