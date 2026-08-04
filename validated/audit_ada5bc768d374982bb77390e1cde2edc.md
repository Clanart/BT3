Based on the code found, there's a concrete decimal-scaling asymmetry bug in `pallet-hyper-fungible-token`, distinct from the "no mint/burn, fixed supply" report but analogous in spirit: a value-conservation invariant that silently breaks under a specific configuration, without any privileged actor being malicious.

### Title
Asymmetric decimal scaling in `convert_to_erc20`/`convert_to_balance` breaks amount conservation across the bridge when local decimals exceed remote decimals - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`convert_to_erc20` and `convert_to_balance` in [1](#0-0)  only scale correctly when `erc_decimals >= local_decimals`. Both functions compute the decimal delta with `erc_decimals.saturating_sub(local_decimals)`, which silently clamps to `0` when `local_decimals > erc_decimals`. In that configuration, `convert_to_erc20` fails to divide down the outgoing amount, and `convert_to_balance` fails to divide down the incoming amount — the "1:1 no rescale" branch is used where a real rescale was required.

### Finding Description
`send()` in [2](#0-1)  escrows/burns the local asset at local-chain precision, then calls `convert_to_erc20(amount, erc_decimals, decimals)` to compute the `message.amount` field posted to the destination contract. `on_accept` in [3](#0-2)  does the mirrored `convert_to_balance` call when minting/releasing on receipt, and `on_timeout` in [4](#0-3)  uses the same conversion when refunding.

The `Precisions` map is populated per `(asset_id, StateMachine)` pair by `register_token`/`update_token` with no validation constraining `erc_decimals` to be `>= local_decimals`. Whenever an operator registers a token where the local asset (e.g. a `pallet-assets` asset or the native currency at 12/18 decimals) has more decimal places than the paired EVM token (e.g. a 6-decimal stablecoin like USDC), `erc_decimals.saturating_sub(local_decimals)` clamps to `0`. `convert_to_erc20` then emits `message.amount = value` (unscaled, still at local-chain magnitude) instead of `value / 10^(local_decimals - erc_decimals)`, inflating the on-wire amount by orders of magnitude relative to what was actually escrowed/burned. Symmetrically, `convert_to_balance` on the return leg fails to divide the incoming ERC20 amount down, over-crediting the beneficiary relative to what should be minted/released for the on-chain equivalent value.

### Impact Explanation
This corrupts the exact value that crosses the chain boundary — the same "amount must move exactly once and only to the rightful beneficiary and amount" invariant called out in the Hyperbridge pivots. Depending on which side is misconfigured, this manifests as: (a) the destination-side `HyperFungibleToken`/`WrappedHyperFungibleToken` contract minting/releasing far more ERC20 tokens than the value actually escrowed on the substrate side (drains the wrapper's locked balance or over-mints unbacked supply), or (b) the substrate-side pallet crediting far more of the local asset on `on_accept`/`on_timeout` than was legitimately bridged, directly inflating a user's balance beyond what should exist — a fund-creation/fund-loss bug reachable via the pallet's own public, unprivileged `send` extrinsic once such a token pair is registered.

### Likelihood Explanation
This requires a specific token registration (`local_decimals > erc_decimals`) which is under the `CreateOrigin` (governance/admin), not an ordinary user. However, once that pairing exists — which is a realistic real-world configuration (e.g., bridging a Polkadot asset with 18 decimals against a 6-decimal EVM stablecoin) — every signed, unprivileged `send()` call by any user triggers the bad scaling path automatically; no relayer, prover, or admin misbehavior is needed to exploit it, only the pre-existing configuration.

### Recommendation
Fix `convert_to_erc20`/`convert_to_balance` to handle both directions symmetrically (divide when `local_decimals > erc_decimals`, multiply when `erc_decimals > local_decimals`), and add a defensive invariant check/test in `register_token`/`update_token` (or in the conversion helpers themselves) so any decimal-pair configuration is exercised by both scale-up and scale-down code paths, with unit tests explicitly covering `local_decimals > erc_decimals`.

### Proof of Concept
1. Governance registers a non-native asset with `local_decimals = 18` (e.g. a `pallet-assets` asset) and configures `Precisions::<T>::insert(asset_id, StateMachine::Evm(1), 6)` (erc_decimals = 6, matching a USDC-like destination token).
2. A user calls `send(SendParams { asset_id, amount: 1_000_000_000_000_000_000 /* 1 token, 18 decimals */, destination: Evm(1), .. })`. The pallet burns `1e18` units locally via `Assets::burn_from` in [5](#0-4) .
3. `convert_to_erc20(1e18, erc_decimals=6, local_decimals=18)` computes `10u128.pow(6u8.saturating_sub(18u8) as u32)` = `10u128.pow(0)` = `1`, so `erc20_amount = 1e18` is embedded as `message.amount`, instead of the correct `1e18 / 10^12 = 1_000_000` (1 USDC-equivalent unit).
4. The destination `HyperFungibleToken` contract mints/releases `1e18` raw units to the recipient — a 10^12x amount inflation relative to the single token burned on the source chain, confirmed against [6](#0-5) .

### Citations

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L254-295)
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
