### Title
Mutable local asset decimals break the `Precisions` invariant in `pallet-hyper-fungible-token`, causing inflated cross-chain mint/release amounts - ([File: modules/pallets/hyper-fungible-token/src/impls.rs])

### Summary
`pallet-hyper-fungible-token`'s decimal-scaling helpers assume `erc_decimals >= local_decimals` forever, an invariant enforced only once, at `register_token`/`update_token` time. The local asset's decimals are read live from `pallet-assets` metadata (`fungibles::metadata::Inspect::decimals`) on every `send`, `on_accept`, and `on_timeout` call, but `pallet-assets` metadata (including `decimals`) is mutable by the asset owner via `set_metadata` after registration. If local decimals are later increased past the registered `erc_decimals`, the `saturating_sub` in the scaling helpers silently clamps to zero instead of erroring or scaling down, corrupting the cross-chain amount by orders of magnitude — directly analogous to `ERC4626Oracle` trusting `IERC4626.decimals()` as a stand-in for a relationship that isn't actually guaranteed to hold.

### Finding Description
`convert_to_balance` and `convert_to_erc20` in [1](#0-0)  compute a scaling exponent as `erc_decimals.saturating_sub(local_decimals)`. This is only correct when `erc_decimals >= local_decimals` always holds; if `local_decimals > erc_decimals`, `saturating_sub` returns `0`, so the function returns the raw, unscaled value instead of dividing it down — a silent corruption rather than a revert.

That invariant is checked only at asset (re-)registration time: [2](#0-1) [3](#0-2) 

But at actual usage time (`send`, `on_accept`, `on_timeout`), the pallet re-fetches the local asset's decimals fresh from `pallet-assets` metadata rather than from any pinned/cached value: [4](#0-3) [5](#0-4) [6](#0-5) 

`pallet-assets` metadata (name/symbol/**decimals**) is mutable after asset creation by the asset's owner via `set_metadata` — a standard, non-privileged extrinsic available to whoever owns the asset (asset creation/ownership in `pallet-assets` is typically permissionless, gated only by a deposit). Nothing in `pallet-hyper-fungible-token` re-validates `erc_decimals >= local_decimals` before scaling, nor freezes/locks the asset's metadata once registered. The README documents the design assumption ("Decimals between this chain and each remote chain may differ; per-pair `Precisions` storage records the EVM-side decimals so amounts get scaled at the boundary") [7](#0-6)  but the `ErcDecimalsBelowLocal` guard is a registration-time-only check [8](#0-7) , not an always-true runtime invariant.

The corrupted value is the scaling exponent `erc_decimals.saturating_sub(local_decimals)` used inside `convert_to_erc20`/`convert_to_balance`, which silently becomes `0` instead of the (impossible-to-represent-as-non-negative) required negative shift, once `local_decimals` exceeds the registered `erc_decimals`.

### Impact Explanation
When `local_decimals` exceeds `erc_decimals` at `send` time, `convert_to_erc20` fails to scale down the outgoing amount, so the ISMP `Message.amount` field encodes a value that is `10^(local_decimals - erc_decimals)` times larger than intended. The destination `HyperFungibleToken`/`WrappedHyperFungibleToken` EVM contract trusts this encoded amount directly when releasing/minting tokens to the beneficiary. This lets a user who escrows or burns a small amount on the substrate side trigger the release/minting of a vastly larger amount on the EVM side — directly stealing tokens from custody/escrow or over-minting a wrapped asset, at the expense of the token gateway's custody account and other holders. The same corruption applies in reverse (`convert_to_balance` on `on_accept`/`on_timeout`), potentially under-crediting or mis-crediting beneficiaries on refunds/receipts. This is a direct fund-theft / unauthorized-mint primitive, matching the bounty's "stealing or loss of funds" and "transaction manipulation" categories.

### Likelihood Explanation
The attack requires only that: (1) some asset the attacker owns/controls in `pallet-assets` gets registered in `pallet-hyper-fungible-token` (a normal onboarding step, not attacker-controlled but not exotic either — any token the attacker created and had listed), and (2) the attacker, as the asset's owner, later calls the standard `pallet-assets::set_metadata` extrinsic to raise the asset's `decimals` value above the registered `erc_decimals`. No relayer, prover, governance, or leaked-key assumption is needed — `set_metadata` is a routine, owner-only, non-privileged call in `pallet-assets`, fully independent of Hyperbridge's own `CreateOrigin` gating. The bug is a silent numeric corruption (no revert), making it easy to trigger unnoticed via a normal `send()` call.

### Recommendation
- Re-validate `erc_decimals >= local_decimals` at the point of use (`send`, `on_accept`, `on_timeout`), not only at `register_token`/`update_token`, and reject the operation (rather than silently truncating) if the invariant no longer holds.
- Alternatively, snapshot/pin the local asset's decimals in pallet storage at registration time instead of re-reading live, mutable `pallet-assets` metadata on every cross-chain operation.
- Make `convert_to_balance`/`convert_to_erc20` fail closed (return an error) when `local_decimals > erc_decimals`, instead of using `saturating_sub`, which masks the underflow as a no-op scale factor.
- Consider locking/freezing metadata (or requiring `CreateOrigin`/governance re-approval) for any asset that is currently registered in the gateway, so its owner cannot unilaterally break the pinned precision assumption after listing.

### Proof of Concept
1. Attacker creates/owns asset `X` in `pallet-assets` with `decimals = 6` and gets it registered via `register_token` with `ChainConfig { decimals: 18 }` for an EVM destination (passes `ErcDecimalsBelowLocal` check: `18 >= 6`).
2. Attacker calls `pallet_assets::set_metadata(X, name, symbol, decimals = 20)` — permitted because they are the asset owner. Now `local_decimals (20) > erc_decimals (18)`.
3. Attacker calls `hyper_fungible_token::send(asset_id = X, amount = 1_000_000 (i.e., 1 unit at decimals=20), destination, ...)`.
4. Inside `send`, `decimals = <Assets as Inspect>::decimals(X) = 20`, `erc_decimals = Precisions::get(X, dest) = 18`.
5. `convert_to_erc20(1_000_000, erc_decimals=18, local_decimals=20)` computes `18u8.saturating_sub(20) = 0`, so `erc20_amount = 1_000_000 * 10^0 = 1_000_000` — instead of the mathematically correct `1_000_000 / 10^2 = 10_000` (scaling a 20-decimal value down to 18-decimal precision).
6. The outgoing `Message.amount = 1_000_000` (in 18-decimal ERC20 units) is dispatched to the destination `HyperFungibleToken` contract, which mints/releases `1_000_000` (10^18-scaled) tokens to the attacker's recipient — 100x the value that should correspond to the escrowed/burned `1_000_000` (10^20-scaled) local units, draining excess value from the gateway's counterpart custody.

Note: I was unable to fully inspect the EVM-side `HyperFungibleTokenImpl.sol`/`HyperFungibleToken.sol` `onAccept` mint/release logic within the available iterations to confirm it performs no independent decimals cross-check against the `Message.amount` field; this assumption (that the EVM side trusts the encoded amount at face value in its own `erc_decimals`) is based on the pallet's own precision-scaling documentation and design intent, and should be verified directly against `sdk/packages/core/contracts/apps/HyperFungibleToken.sol` before remediation.

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L219-223)
```rust
		/// Peer chain is not an EVM state machine; this pallet bridges substrate <-> EVM only
		NonEvmPeerChain,
		/// Configured ERC decimals are less than the local asset's decimals; precision conversion
		/// requires erc_decimals >= local_decimals
		ErcDecimalsBelowLocal,
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L267-290)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L390-406)
```rust
			let local_decimals = if update.asset_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					update.asset_id.clone(),
				)
			};

			for (chain, config) in update.add_chains {
				// This pallet bridges substrate <-> EVM only; reject non-EVM peers.
				if !matches!(chain, StateMachine::Evm(_)) {
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
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

**File:** modules/pallets/hyper-fungible-token/README.md (L30-32)
```markdown
Decimals between this chain and each remote chain may differ; per-pair
`Precisions` storage records the EVM-side decimals so amounts get scaled at
the boundary.
```
