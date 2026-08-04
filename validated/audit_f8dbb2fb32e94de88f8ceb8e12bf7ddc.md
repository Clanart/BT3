## Analysis Summary

The MANTRA report's core broken invariant is: **an unvalidated, mutable "decimals" value used to scale cross-domain amounts, where the value can drift from what the protocol assumed at setup time, corrupting settlement math.** The closest local analog in Hyperbridge is in `pallet-hyper-fungible-token`, where the ERC20-side decimals (`Precisions`) are pinned once by governance at `register_token` time, but the **local-side decimals are re-read live from mutable `pallet-assets` metadata on every send/receive/timeout** — a value the asset's own (non-privileged) owner can change at any time after registration.

### Title
Live re-read of mutable `pallet-assets` decimals metadata in `hyper-fungible-token` allows an unprivileged asset owner to desynchronize cross-chain amount scaling and mint/credit wrong amounts - (File: modules/pallets/hyper-fungible-token/src/module.rs)

### Summary
`register_token`/`update_token` pin the **remote (ERC20) decimals** into `Precisions` storage and enforce `config.decimals >= local_decimals` only once, at registration time [1](#0-0) . However, the **local decimals** used in every subsequent `send`, `on_accept`, and `on_timeout` call are not cached — they are re-fetched live via `<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(local_asset_id)` [2](#0-1) . `pallet-assets` metadata (including `decimals`) is set/mutated by the asset's `Owner`/`Issuer`, an account that is unrelated to `T::CreateOrigin` and, depending on runtime config, can be the original (unprivileged) asset creator. This lets that owner unilaterally change the local decimals after the bridge's `Precisions` invariant was established, without going through `update_token`.

### Finding Description
The invariant `config.decimals >= local_decimals` is checked only once during `register_token`/`update_token` [3](#0-2) . The `local_decimals` value at that moment is derived from `T::Assets::decimals(asset_id)`, i.e., `pallet-assets` metadata that the asset owner controls independently of the bridge's `CreateOrigin`.

Every subsequent cross-chain operation (`send`, `on_accept`, `on_timeout`) re-derives `decimals` from the same live, mutable metadata source rather than from a value pinned at registration time [4](#0-3) [5](#0-4) . The stored `Precisions` (remote/ERC20 decimals) never changes unless governance calls `update_token`, but the local decimals can silently change underneath it.

`convert_to_balance`/`convert_to_erc20` compute scaling purely from the two decimals values passed in, using `saturating_sub`, with no bounds re-validation against the value originally enforced at registration [6](#0-5) . If the asset owner changes `decimals` after registration (e.g., from 6 to 18, or to 0), the `erc_decimals - local_decimals` exponent used to scale incoming/outgoing/refunded amounts changes dramatically, while the actual custodied/escrowed token balances (locked via `NativeCurrency`/`Assets::transfer`/`burn_from`) were computed under the old decimals assumption.

### Impact Explanation
This directly threatens custody correctness of bridged funds:
- On `send`, the local balance debited (locked/burned) uses the caller's raw `params.amount` in local units, but the ERC20-side amount dispatched cross-chain (`convert_to_erc20`) is scaled using the now-drifted `decimals` value, producing an ERC20 amount inconsistent with what was actually escrowed [7](#0-6) .
- On `on_accept` (inbound mint/release), a manipulated `decimals` value causes `convert_to_balance` to mint or release a local amount that no longer matches the intended cross-chain value, resulting in over-minting (fund creation out of thin air / loss for the custody pool) or under-crediting the beneficiary [8](#0-7) .
- On `on_timeout` (refund), the same drift causes refunds of the wrong magnitude relative to what was originally escrowed [9](#0-8) .

This is a genuine "wrong amount" / fund-loss vector reachable by an unprivileged actor (the asset owner) without needing a malicious relayer, prover, or governance compromise — it only requires the asset owner to call the standard `pallet-assets::set_metadata` extrinsic they already have rights to.

### Likelihood Explanation
Exploitability depends on whether the runtime's `pallet-assets` instance lets the token's Owner mutate `decimals` metadata independently of `T::CreateOrigin` used by `pallet-hyper-fungible-token`. This is the standard `pallet-assets` permission model (`Owner`/`ForceOrigin` distinct from arbitrary bridge governance), and nothing in `register_token`/`update_token` re-pins or freezes the asset's metadata once registered. The check at line 352-355 gives a false sense that the relationship is enforced permanently, when it is only a point-in-time check. I could not fully verify from the indexed code whether the specific deployed `pallet-assets` instance freezes metadata for bridge-registered assets (e.g., via `Asset::is_frozen`), so this should be confirmed against the actual runtime configuration.

### Recommendation
- Pin `local_decimals` into a dedicated storage item at `register_token` time (alongside `Precisions`), instead of re-reading live `pallet-assets` metadata on every `send`/`on_accept`/`on_timeout`.
- Alternatively, call `pallet_assets::freeze_metadata` (or equivalent) on any asset registered through `register_token`, and/or re-validate `config.decimals >= local_decimals` on every cross-chain operation, aborting if it no longer holds.

### Proof of Concept
1. Asset owner creates a `pallet-assets` asset `X` with `decimals = 6` and mints/holds a balance.
2. Governance (`CreateOrigin`) calls `register_token` for asset `X`, setting `Precisions[X][EVM] = 18` (passes the check `18 >= 6`) [10](#0-9) .
3. The asset owner calls `pallet_assets::set_metadata` for asset `X`, changing `decimals` to `0`.
4. Owner calls `send` with `amount = 1_000_000` (previously worth 1.0 token at 6 decimals); the local `Assets::burn_from`/`transfer` debits `1_000_000` raw units, but `convert_to_erc20(1_000_000, erc_decimals=18, decimals=0)` now scales by `10^18` instead of `10^12`, dispatching an ERC20 amount `10^6` times larger than intended to the destination chain [7](#0-6) [11](#0-10) , allowing the destination chain to release/mint a far larger amount than what was actually escrowed.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L254-301)
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

			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

			let token_message = Message {
				from: sender.to_vec().into(),
				to: params.recipient.to_vec().into(),
				amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
				data: params.call_data.unwrap_or_default().into(),
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L336-367)
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
				let token_contract = config.token_contract.0.to_vec();
				TokenContracts::<T>::insert(
					chain,
					registration.local_id.clone(),
					token_contract.clone(),
				);
				ContractToAsset::<T>::insert(
					chain,
					token_contract,
					registration.local_id.clone(),
				);
				Precisions::<T>::insert(registration.local_id.clone(), chain, config.decimals);
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L74-83)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L84-117)
```rust
		let amount = convert_to_balance::<
			<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
		>(
			U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
			erc_decimals,
			decimals,
		)
		.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;

		// Mint or transfer to beneficiary
		if local_asset_id == T::NativeAssetId::get() {
			<T as Config>::NativeCurrency::transfer(
				&Pallet::<T>::pallet_account(),
				&beneficiary,
				amount,
				ExistenceRequirement::AllowDeath,
			)
			.map_err(|e| HftError::TransferFailed(e.into()))?;
		} else {
			let is_native = NativeAssets::<T>::get(local_asset_id.clone());
			if is_native {
				<T as Config>::Assets::transfer(
					local_asset_id,
					&Pallet::<T>::pallet_account(),
					&beneficiary,
					amount.into(),
					Preservation::Expendable,
				)
				.map_err(|e| HftError::TransferFailed(e.into()))?;
			} else {
				<T as Config>::Assets::mint_into(local_asset_id, &beneficiary, amount.into())
					.map_err(|e| HftError::MintFailed(e.into()))?;
			}
		}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L239-285)
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

				// Refund: release escrowed tokens back to the original sender
				if local_asset_id == T::NativeAssetId::get() {
					<T as Config>::NativeCurrency::transfer(
						&Pallet::<T>::pallet_account(),
						&beneficiary,
						amount.into(),
						ExistenceRequirement::AllowDeath,
					)
					.map_err(|e| HftError::TransferFailed(e.into()))?;
				} else {
					let is_native = NativeAssets::<T>::get(local_asset_id.clone());
					if is_native {
						<T as Config>::Assets::transfer(
							local_asset_id,
							&Pallet::<T>::pallet_account(),
							&beneficiary,
							amount.into(),
							Preservation::Expendable,
						)
						.map_err(|e| HftError::TransferFailed(e.into()))?;
					} else {
						<T as Config>::Assets::mint_into(
							local_asset_id,
							&beneficiary,
							amount.into(),
						)
						.map_err(|e| HftError::MintFailed(e.into()))?;
					}
				}
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
