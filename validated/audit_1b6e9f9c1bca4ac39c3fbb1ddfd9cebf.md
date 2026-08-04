## Finding Summary

I traced the decimal conversion path across `send`, `on_accept`, and `on_timeout` in `pallet_hyper_fungible_token`. The core issue is that the invariant `erc_decimals >= local_decimals` — which is required for `convert_to_erc20`'s `saturating_sub` scaling to be safe — is validated only once, at `register_token`/`update_token` time, against the asset's decimals *at that moment*. The actual conversion functions in `send`, `on_accept`, and `on_timeout` do not re-validate this invariant; they instead re-fetch the local asset's decimals live from the `Assets` pallet's metadata at call time.

### Title
Decimal invariant is enforced only at registration, not re-validated at conversion time, allowing drift between `Precisions` and live asset decimals - (File: modules/pallets/hyper-fungible-token/src/lib.rs)

### Finding Description
`register_token` and `update_token` check `config.decimals >= local_decimals` once, using the asset's decimals value observed at that call: [1](#0-0) 

But `send` re-derives `decimals` independently and live at call time via `fungibles::metadata::Inspect::decimals`, rather than snapshotting/validating against the stored `Precisions` value: [2](#0-1) 

The scaling itself uses `saturating_sub`, which silently clamps to zero (no scaling) instead of erroring, if the assumed ordering (`erc_decimals >= decimals`) is violated: [3](#0-2) 

The same pattern (live decimal lookup, no re-check against the registration-time invariant) repeats in `on_accept` and `on_timeout`: [4](#0-3) [5](#0-4) 

This is exactly the "partial state change" scenario the question describes: `Precisions::<T>` (set once at registration, `StateMachine`/`AssetId`-keyed) is one piece of state that stays fixed, while the local asset's decimals metadata in the `Assets` pallet is a second, independent piece of state that can potentially change later without updating `Precisions`. If the asset's decimals metadata increases after registration (e.g., to a value greater than the previously configured `erc_decimals`), then in `send`, `erc_decimals.saturating_sub(decimals)` becomes `0`, so `convert_to_erc20` multiplies by `10^0 = 1` instead of properly scaling — the outgoing ERC20 amount is computed as if local and remote decimals were equal, even though they are not. This produces an ERC20-denominated amount that no longer corresponds to the economic value actually escrowed/burned locally, in violation of the stated invariant that "precision conversion must preserve economic value across send, receive, and timeout."

### Impact Explanation
If reachable by an unprivileged actor (i.e., if the local asset's decimals metadata can be changed independently of the pallet's `CreateOrigin`-gated `register_token`/`update_token` calls — for example, by the asset's own admin/owner in `pallet_assets`, which for permissionlessly-created assets can be an ordinary user), this allows a user to burn/escrow a small local amount while dispatching a message that credits a disproportionately large amount on the destination EVM `HyperFungibleToken`/`WrappedHyperFungibleToken` contract, i.e., wrongful minting of bridged value on the destination chain. This matches the "Critical: wrongful mint... of protocol-controlled or user escrowed assets" impact category.

### Likelihood Explanation
Likelihood depends entirely on whether an unprivileged principal can mutate the local asset's decimals metadata after `register_token` runs — I was not able to confirm this from the indexed code in this session. I could not locate the runtime's `impl pallet_assets::Config for Runtime` block (for `gargantua`/`nexus`) to determine the `Config::Admin`/set-metadata origin or whether asset creation is permissionless in production. If decimals metadata is immutable post-creation, or if only the same privileged `CreateOrigin`/root can alter it, this path is not reachable by an unprivileged attacker and would fall under the "requires privileged operators" exclusion in the decision standard.

### Recommendation
Snapshot and store the local asset's decimals at `register_token`/`update_token` time (alongside `Precisions`), and use that stored snapshot — not a live re-fetch — in `send`, `on_accept`, and `on_timeout`. Additionally, replace the silent `saturating_sub` clamp with an explicit `ensure!(erc_decimals >= decimals, Error::<T>::ErcDecimalsBelowLocal)` check inside `send`/`on_accept`/`on_timeout` themselves, so any future drift between stored precision config and live asset metadata causes a hard failure instead of a silent mis-scaled conversion.

### Proof of Concept
Conceptual PoC (not executed, given tool limitations):
1. Register a non-native asset with local decimals = 6 and `erc_decimals` (EVM side) = 18 via `register_token` (passes the `18 >= 6` check).
2. If permitted, change the asset's decimals metadata (via `pallet_assets::set_metadata` or equivalent) to 20, independent of `Precisions::<T>` which remains 18.
3. Call `send(origin, params)` with `asset_id` = this asset. `decimals` is now live-fetched as 20; `erc_decimals` remains 18 from `Precisions`. `erc_decimals.saturating_sub(decimals) = 0`, so `convert_to_erc20` returns `amount` unscaled instead of the correct `amount / 10^2`.
4. The dispatched `Message.amount` on the destination EVM contract is 100x larger (in ERC20 18-decimal terms) than what was actually burned locally, while the burn itself only removed the smaller, correctly-decimal-adjusted amount from the sender.

Because I could not verify the mutability/permission model of asset decimals metadata in this codebase, I present this with that caveat rather than as a fully confirmed exploit chain.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L254-290)
```rust
			let erc_decimals = Precisions::<T>::get(params.asset_id.clone(), params.destination)
				.ok_or(Error::<T>::DecimalsNotFound)?;

			// Lock or burn the local asset
			let decimals = if params.asset_id == T::NativeAssetId::get() {
				// escrow the native asset
				<T as Config>::NativeCurrency::transfer(
					&who,
					&Self::pallet_account(),
					params.amount,
					ExistenceRequirement::AllowDeath,
				)?;
				T::Decimals::get()
			} else {
				let is_native = NativeAssets::<T>::get(params.asset_id.clone());
				if is_native {
					<T as Config>::Assets::transfer(
						params.asset_id.clone(),
						&who,
						&Self::pallet_account(),
						params.amount.into(),
						Preservation::Expendable,
					)?;
				} else {
					<T as Config>::Assets::burn_from(
						params.asset_id.clone(),
						&who,
						params.amount.into(),
						Preservation::Expendable,
						Precision::Exact,
						Fortitude::Polite,
					)?;
				}
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					params.asset_id.clone(),
				)
			};
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L336-355)
```rust
			let local_decimals = if registration.local_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					registration.local_id.clone(),
				)
			};

			NativeAssets::<T>::insert(registration.local_id.clone(), registration.native);

			let chains: Vec<StateMachine> = registration.chains.keys().cloned().collect();
			for (chain, config) in registration.chains {
				// This pallet bridges substrate <-> EVM only; reject non-EVM peers.
				if !matches!(chain, StateMachine::Evm(_)) {
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-59)
```rust
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
