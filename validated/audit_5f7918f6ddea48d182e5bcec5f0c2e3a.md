### Title
Integer-division truncation in `convert_to_balance` silently destroys bridged funds on every non-exact-scale HyperFungibleToken transfer - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
The `pallet-hyper-fungible-token` converts an incoming ERC20-denominated amount to the local Substrate balance by integer division, exactly the same rounding pattern that caused the referenced `Bonding.sol` M-05 finding. Unlike the outgoing path (which only ever scales *up* by multiplication and cannot lose precision), the inbound path scales *down* by division and has no remainder/representability check, so any amount that isn't an exact multiple of `10^(erc_decimals - local_decimals)` has its fractional remainder permanently destroyed — burned/locked on the source chain but never minted/released on the destination chain, and never refunded to anyone.

### Finding Description
`convert_to_balance` performs a floor division with no remainder check: [1](#0-0) 

This is invoked from the `on_accept` handler when tokens arrive from a peer `HyperFungibleToken`/`WrappedHyperFungibleToken` contract: [2](#0-1) 

and again from `on_timeout` when refunding the original sender: [3](#0-2) 

On the outgoing side (`send`), the local amount is converted to ERC20 units with `convert_to_erc20`, a pure multiplication that never loses precision as long as `erc_decimals >= local_decimals` (enforced by `ErcDecimalsBelowLocal`): [4](#0-3) 

So the full-precision amount is what gets locked/burned on the EVM side (the EVM `Message.amount` is the exact wei value the user sent — see the `Message` struct in `HyperFungibleToken.sol`, which carries a plain `uint256 amount` with no scaling). When that same wei-precision value comes back through `on_accept`/`on_timeout` and is divided down to the (typically lower-precision) Substrate asset, e.g. `erc_decimals=18`, `local_decimals=12`, dividing by `10^6` truncates. Unlike the EVM `BandwidthManager.purchase()` path in this same codebase, which explicitly reverts with `PriceNotRepresentable()` when a scaled value doesn't divide cleanly: [5](#0-4) 

`convert_to_balance` has **no equivalent guard** — it silently floors and proceeds to mint/transfer the truncated amount, with the remainder unaccounted for anywhere in storage or events.

### Impact Explanation
This is a direct, unconditional loss-of-funds bug matching the bounty's "stealing or loss of funds" category: every cross-chain transfer whose ERC20 amount is not an exact multiple of `10^(erc_decimals - local_decimals)` results in the beneficiary receiving strictly less value than was escrowed/burned on the source chain, and the same truncation applies to timeout refunds, meaning the *original sender* also gets shorted when a message times out. Because HyperFungibleToken/`WrappedHyperFungibleToken` per-token contracts are meant to be deployed for arbitrary tokens with differing decimals (the docs explicitly note decimals commonly differ, e.g. 18 on EVM vs 12 configured natively), this is not a rare edge case — it triggers whenever a user picks an amount whose low-order digits don't align with the decimal delta, which for typical human-chosen amounts (e.g. anything with cents-level precision bridged into a lower-decimal asset) is common.

### Likelihood Explanation
No privileged actor, relayer misbehavior, or malicious peer is required. Any unprivileged user calling `HyperFungibleToken.send()`/`WrappedHyperFungibleToken.send()` with an amount that doesn't divide evenly by the decimal-scale factor triggers the loss automatically and deterministically on delivery (`on_accept`) or on timeout refund (`on_timeout`). There is no existing check analogous to `PriceNotRepresentable()` to prevent or reject such transfers before value is locked/burned on the source side.

### Recommendation
Add a representability check in `convert_to_balance` mirroring `BandwidthManager.sol`'s `total18d % scale != 0` guard: reject the message (or refund the full source-side amount inclusive of dust) when `value % 10^(erc_decimals - local_decimals) != 0`, rather than silently flooring. Alternatively, track and credit/refund the truncated remainder so it isn't destroyed, or require that the pallet only register tokens with `local_decimals == erc_decimals` to avoid the conversion entirely.

### Proof of Concept
1. Register a token with `native = true`/`false`, `erc_decimals = 18` (destination EVM chain) and local pallet `decimals = 12` (or any `Precisions` entry with `erc_decimals > decimals`).
2. From the EVM `HyperFungibleToken`/`WrappedHyperFungibleToken` contract, call `send()` with `amount = 1_000_000_000_000_000_001` wei (18-decimals), i.e. `10^18 + 1`. The contract locks/burns the full `10^18 + 1`.
3. On delivery, `on_accept` computes `decimals=12`, `erc_decimals=18`, so `convert_to_balance` divides by `10^(18-12) = 10^6`: `(10^18 + 1) / 10^6 = 999999999999` (floors), discarding the `+1` remainder (and, more generally, discarding up to `10^6 - 1` units for arbitrary inputs).
4. The beneficiary is minted/transferred `999999999999` local units — less value than the `10^18 + 1` wei that was actually escrowed/burned on the EVM side — and the difference is never recovered by anyone.
5. The same effect reproduces in `on_timeout`, causing refunds to also be short-changed.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-52)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L82-117)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L246-285)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-295)
```rust
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
```

**File:** evm/src/apps/BandwidthManager.sol (L156-161)
```text
        uint256 total18d = price18d * months;
        address feeToken = IDispatcher(_host).feeToken();
        uint8 dec = IERC20Metadata(feeToken).decimals();
        uint256 scale = 10 ** (18 - dec);
        if (total18d % scale != 0) revert PriceNotRepresentable();
        uint256 amount = total18d / scale;
```
