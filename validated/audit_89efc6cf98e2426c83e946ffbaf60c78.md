## Analysis

The external report's core broken invariant is: **code assumes a fixed decimal relationship between two token representations, computes a scaling factor from that assumption, and never re-validates it at the point of value transfer — so a drift in actual decimals causes silent mis-scaling of the transferred amount rather than a revert.**

Searching Hyperbridge for the analogous pattern, the strongest hit is the decimal-scaling logic in `pallet-hyper-fungible-token`, which bridges assets between this substrate chain and paired EVM `HyperFungibleToken` contracts.

### The broken invariant

`register_token`/`update_token` are the *only* places that enforce `erc_decimals >= local_decimals` (`Error::ErcDecimalsBelowLocal`), checked once at registration time against a **live** read of the local asset's decimals: [1](#0-0) 

But the actual value conversion at transfer time re-queries local decimals live from `pallet-assets` every single call and feeds it into `saturating_sub`, which **silently clamps to zero** instead of failing when the assumption (`erc_decimals >= local_decimals`) no longer holds: [2](#0-1) 

`send()` calls `convert_to_erc20(amount, erc_decimals, decimals)` where `decimals` is fetched live via `fungibles::metadata::Inspect::decimals(asset_id)`: [3](#0-2) 

`on_accept` and `on_timeout` do the same live lookup and feed it into `convert_to_balance`: [4](#0-3) 

### Why the guard doesn't hold

`pallet-assets` metadata (including `decimals`) is mutable after asset creation via `set_metadata`, callable by the asset's `Issuer`/`Owner` — an ordinary, non-privileged, non-Hyperbridge-governance account for any permissionlessly-created asset. The `ErcDecimalsBelowLocal` check in `register_token` validates the relationship only at the moment governance onboards the asset for bridging; nothing in `send`, `on_accept`, or `on_timeout` re-checks it. Once the asset owner (who is not a bridge admin, relayer, or prover) bumps `decimals` upward after registration, `erc_decimals.saturating_sub(local_decimals)` becomes `0`, and `convert_to_erc20`/`convert_to_balance` stop scaling entirely instead of erroring.

### Title
Stale decimals invariant lets the asset owner silently break cross-chain amount scaling in `pallet-hyper-fungible-token` - (File: modules/pallets/hyper-fungible-token/src/impls.rs)

### Summary
`register_token`/`update_token` enforce `erc_decimals >= local_decimals` once, at registration, using a snapshot of the local asset's decimals read from `pallet-assets`. `send`, `on_accept`, and `on_timeout` instead re-read decimals live on every call and pass them through `convert_to_erc20`/`convert_to_balance`, which use `saturating_sub` to compute the scaling exponent. If the underlying asset's decimals are later increased (via the ordinary, non-bridge-privileged `pallet-assets::set_metadata` call available to the asset's owner) so that `local_decimals > erc_decimals`, the exponent saturates to `0` and the functions stop scaling the amount at all, instead of reverting.

### Finding Description
`convert_to_erc20`/`convert_to_balance` compute the scaling factor as `10 ** erc_decimals.saturating_sub(local_decimals)`. [5](#0-4) 
This formula is only correct when `erc_decimals >= local_decimals`, which is exactly the invariant `register_token`/`update_token` assert — but only at the moment of registration: [1](#0-0) 
`send()` fetches `decimals` live from `fungibles::metadata::Inspect` and feeds it straight into `convert_to_erc20` without re-checking the invariant: [6](#0-5) 
Since `pallet-assets` metadata (`decimals`) is not immutable — the asset's `Issuer`/`Owner` can call `set_metadata` to change it after creation, and that owner is not a Hyperbridge admin, relayer, or prover — an ordinary asset owner can move `local_decimals` above the `erc_decimals` value frozen in `Precisions` storage at registration time. On the next `send()`, `erc_decimals.saturating_sub(local_decimals)` evaluates to `0`, so the locally-locked/burned amount (expressed in the now-inflated local decimal precision) is forwarded **unscaled** as the raw ERC20 `amount` field to the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract, which still expects amounts scaled to the old, smaller `erc_decimals`.

### Impact Explanation
This is a direct value-creation bug: a user burns/locks `N` local units at the (now-inflated) local decimal precision, but the destination EVM contract mints/releases `N` raw units interpreted at the smaller, still-registered `erc_decimals`. Since `erc_decimals < local_decimals`, this represents `10**(local_decimals - erc_decimals)` times more value than was actually escrowed, effectively minting/releasing far more tokens on the destination chain than were locked on the source chain — a "false state acceptance" / fund-creation outcome matching the bounty's "stealing or loss of funds" and "transaction manipulation" categories. The reverse direction (`on_accept`/`on_timeout`) causes symmetric under-crediting, permanently locking user funds.

### Likelihood Explanation
The only precondition is that the asset's decimals be increased after Hyperbridge governance has already onboarded it for bridging via `register_token`. That mutation is performed through `pallet-assets::set_metadata`, which is gated by the asset's own `Issuer`/`Owner` origin — not by Hyperbridge's `CreateOrigin`, a relayer, or a prover. For any bridged asset whose creation and metadata rights were not additionally locked down by the runtime (e.g. an asset created permissionlessly and later registered for bridging), the asset owner can independently trigger this drift at will, at any time after onboarding, with a single ordinary extrinsic. No malicious relayer, prover, or Hyperbridge governance compromise is required.

### Recommendation
Re-validate `erc_decimals >= local_decimals` (or better, snapshot and pin `local_decimals` into `Precisions` storage at registration time and require it to be re-provided/checked on later reads) inside `send`, `on_accept`, and `on_timeout`, and return the existing `ErcDecimalsBelowLocal` error instead of silently saturating to `0` when the invariant no longer holds. Alternatively, replace `saturating_sub` with `checked_sub` and propagate an error on underflow so decimal drift fails closed rather than silently mis-scaling value.

### Proof of Concept
1. Governance registers asset `X` (created via permissionless `pallet-assets::create`, owner = Attacker) for bridging to `EVM(1)` with `Precisions[X][EVM(1)] = 6`, satisfying `register_token`'s check because `X.decimals() == 6` at that time. [7](#0-6) 
2. Attacker (asset `Issuer`/`Owner` of `X`, not a bridge admin) calls `pallet-assets::set_metadata(X, name, symbol, decimals=18)`, bumping `X.decimals()` to 18.
3. Attacker calls `HyperFungibleToken::send({ asset_id: X, amount: 1_000_000_000_000_000_000 (1 token at 18 decimals), destination: EVM(1), ... })`. The pallet burns/locks `1e18` units of `X` from the attacker. [8](#0-7) 
4. `convert_to_erc20(1e18, erc_decimals=6, local_decimals=18)` computes `10 ** 6.saturating_sub(18) == 10**0 == 1`, so `erc20_amount = 1e18` (unscaled), instead of the correct `1e18 / 10**12 = 1e6`. [9](#0-8) 
5. The destination `HyperFungibleToken` contract, configured for a 6-decimal token, mints/releases `1e18` raw units to the recipient — 1,000,000× more value than the `1e18`-precision `1` token the attacker actually escrowed, for a net gain of `999,999` tokens' worth of value out of the bridge's custody.

### Citations

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
