Based on the report's core invariant break — a decimals normalization routine that silently produces a wrong scale factor instead of reverting/erroring when the "erc_decimals ≥ local_decimals" assumption is violated — the closest local analog is in `modules/pallets/hyper-fungible-token`.

### Title
Stale decimals invariant lets HyperFungibleToken mis-scale cross-chain amounts after asset metadata changes - ([File: modules/pallets/hyper-fungible-token/src/impls.rs])

### Summary
`register_token` and `update_token` enforce `config.decimals (erc_decimals) >= local_decimals` only at the moment they are called [1](#0-0) , but `local_decimals` for non-native assets is not snapshotted — it is re-read live from the fungible asset's metadata on every `send()`/`on_accept()`/`on_timeout()` call [2](#0-1) [3](#0-2) . `Precisions` (erc_decimals) is a stored value fixed at registration time [4](#0-3) , while local decimals can drift independently after registration if the underlying asset's metadata is later changed.

### Finding Description
The scaling helpers assume `erc_decimals >= local_decimals` and use `saturating_sub` rather than checked arithmetic: [5](#0-4) 

If `local_decimals` ever exceeds the registered `erc_decimals` (the exact inverted case the report's `TokenSale` bug covers, just via drift instead of a bad initial registration), `erc_decimals.saturating_sub(local_decimals)` silently evaluates to `0`. `convert_to_erc20` then stops scaling up at all, and `convert_to_balance` stops scaling down at all — instead of reverting like the original `TokenSaleUSDB`/`TokenSaleETH` bug, this pallet returns a **wrong, non-error value**: a raw local-unit amount is sent to the EVM side as if it were already in `erc_decimals` units, producing a grossly inflated (or, on the way back, grossly deflated) minted/refunded amount at `on_accept`/`on_timeout` — [6](#0-5) [7](#0-6) .

The `ErcDecimalsBelowLocal` guard in `register_token`/`update_token` only prevents *creating* this mismatch through the pallet's own privileged calls; it does nothing to prevent the mismatch arising afterward if the referenced asset's decimals are mutated out-of-band (e.g., via the underlying `Assets` pallet's own metadata-management extrinsic, which in many Substrate configurations can be called by the asset's owner/issuer rather than the hyper-fungible-token pallet's `CreateOrigin`). I was not able to fully verify from the scanned files whether the runtime's concrete `Assets` implementation permits owner-controlled `set_metadata` for the specific assets registered here — this would require inspecting the runtime's `pallet_assets::Config` (`ForceOrigin`/permissionless creation flags), which I could not fully retrieve in the available search results.

### Impact Explanation
If reachable, this breaks the "bridged assets move exactly once and only to the rightful beneficiary and amount" invariant: a burn/lock of a small raw amount on one side could mint or refund a vastly different (larger or smaller) amount on the other side, i.e., unauthorized fund creation/loss across the bridge, without any relayer, prover, or hyper-fungible-token-admin misbehavior — only a change to the referenced asset's own decimals metadata, an action outside this pallet's control.

### Likelihood Explanation
Likelihood is **uncertain/low-to-moderate** and depends on the runtime configuration: it only fires if (a) the concrete `Assets` implementation lets someone other than the hyper-fungible-token `CreateOrigin` mutate an already-registered asset's `decimals`, and (b) the pallet's `Precisions` value is not re-validated afterward. Neither `send()` nor `on_accept()`/`on_timeout()` re-check `erc_decimals >= local_decimals` before applying `convert_to_erc20`/`convert_to_balance`, so if the precondition is broken, every subsequent bridge transfer for that asset silently mis-scales.

### Recommendation
- Replace `saturating_sub` in `convert_to_balance`/`convert_to_erc20` with checked subtraction that errors out (mirroring the report's suggested fix of restricting/normalizing rather than silently no-op scaling).
- Re-validate `erc_decimals >= local_decimals` at the time of every `send()`, `on_accept()`, and `on_timeout()` call, not just at registration, or snapshot/freeze the asset's decimals at registration time so it cannot drift.
- Confirm and, if necessary, lock down which origin can mutate metadata (`decimals`) of assets once they are registered with `pallet-hyper-fungible-token`.

### Proof of Concept
1. Governance registers asset `X` (local decimals = 6) for chain `EVM-1` with `erc_decimals = 6` via `register_token`, passing the `config.decimals >= local_decimals` check [8](#0-7) .
2. The underlying `Assets` pallet's metadata for asset `X` is later changed so `decimals()` now returns 24 (via whatever origin the concrete runtime permits for that extrinsic — not the hyper-fungible-token pallet).
3. A user calls `send()` with `params.asset_id = X`. `decimals` resolves to 24 [2](#0-1) , `erc_decimals` is still the stored 6. `convert_to_erc20(amount, 6, 24)` computes `10^(6.saturating_sub(24)) = 10^0 = 1`, i.e., no upscaling is applied even though 18 orders of magnitude of scaling were expected.
4. The destination EVM `HyperFungibleToken` contract, expecting `erc_decimals = 6` semantics, receives an amount that is orders of magnitude off from what was actually escrowed/burned on the source chain, minting an incorrect amount to the beneficiary.

Given the unresolved question about whether asset-metadata mutation is actually reachable by a non-privileged actor in this runtime, this should be treated as a **plausible but not fully confirmed** local analog; a Devin session with full repo/runtime-config access is recommended to verify the `Assets`/`pallet_assets::Config` origin rules referenced above.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L287-290)
```rust
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					params.asset_id.clone(),
				)
			};
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L75-83)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L248-285)
```rust
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
