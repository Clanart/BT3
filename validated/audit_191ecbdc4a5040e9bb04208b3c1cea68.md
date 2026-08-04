## Analysis

The external report's core defect: components that are supposed to share one behavioral contract silently diverge — one variant returns different data than expected, and nothing catches the divergence until it corrupts downstream state.

The direct local analog is in the `hyper-fungible-token` pallet's decimal-scaling helpers, used to translate amounts between the substrate-side local asset and the EVM-side ERC20 representation: [1](#0-0) 

`convert_to_erc20`/`convert_to_balance` assume `erc_decimals >= local_decimals` and encode that assumption via `saturating_sub`, which silently clamps to `0` (i.e. a no-op multiplier/divisor) instead of erroring when the assumption is violated. This invariant is checked **only** at `register_token`/`update_token` time, using a **live** query of the asset's decimals: [2](#0-1) [3](#0-2) 

But `send()` re-fetches `decimals` live from `fungibles::metadata::Inspect` at call time rather than using a value snapshotted/validated at registration: [4](#0-3) 

If the underlying asset's decimals metadata can increase after registration (pallet-assets generally allows the asset's `Admin`/owner — which is not necessarily the token gateway's governance `CreateOrigin` — to call `set_metadata`), `local_decimals` can exceed the previously-validated `erc_decimals` at `send()` time with no re-check. `convert_to_erc20`'s `saturating_sub(erc_decimals, local_decimals)` then clamps to `0`, so the raw local-precision amount (e.g. 18-decimal units) is sent unscaled as the ERC20 `message.amount`, which the EVM-side contract mints/transfers directly: [5](#0-4) 

This inflates the minted amount by `10^(local_decimals - erc_decimals)` — e.g. locking 1 whole token locally (18 decimals) but minting an amount interpreted at 6 ERC20 decimals as `10^12` tokens on the destination chain.

### Title
Stale decimals validation lets `HyperFungibleToken::send` mint inflated cross-chain amounts - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`convert_to_erc20`/`convert_to_balance` encode the invariant `erc_decimals >= local_decimals` via `saturating_sub`, which silently becomes a no-op scaling factor instead of failing when violated. The invariant is enforced only once, at `register_token`/`update_token`, against a live decimals lookup; `send()` re-reads decimals live but never re-validates the relationship, so any subsequent increase in the local asset's decimals (via the asset's own metadata admin) breaks scaling silently and inflates the amount encoded in the outgoing ISMP message.

### Finding Description
`register_token`/`update_token` require `config.decimals >= local_decimals` computed from a live query of the asset's current decimals metadata: [2](#0-1) 
This check is a point-in-time assertion, not a stored/pinned value. `send()` performs the scaling using a fresh, unvalidated `decimals` lookup: [6](#0-5) 
`convert_to_erc20` computes the scaling exponent as `erc_decimals.saturating_sub(local_decimals)`, which floors to `0` whenever `local_decimals > erc_decimals`, instead of erroring: [7](#0-6) 
If the asset's decimals metadata is later increased above the registered `erc_decimals` (a mutation outside this pallet's control, typically permitted to the asset's own metadata admin via the generic `Assets` implementation), the guard is never re-checked and the amount silently escapes correct scaling.

### Impact Explanation
The corrupted value is `message.amount` inside the dispatched `DispatchPost` — the exact quantity the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract mints/transfers on `onAccept`: [5](#0-4) 
Because the multiplier silently clamps to `1` instead of the intended `10^(erc_decimals-local_decimals)`, the raw local-precision integer is forwarded unscaled and misinterpreted at the destination's (smaller) decimal precision, inflating the credited amount by up to `10^(local_decimals - erc_decimals)`. This is a direct amount-corruption / fund-inflation bug on the settlement path of a bridge asset.

### Likelihood Explanation
Exploitability depends only on the local asset's decimals metadata increasing after `register_token`, which is plausible for any generic `fungibles::Mutate + fungibles::metadata::Inspect` backend where the asset's own admin (not the token gateway's `CreateOrigin`) controls metadata — a configuration drift the pallet does nothing to prevent or re-validate. No relayer/prover misbehavior or protocol-admin action against the token gateway itself is required; a single signed `send()` call surfaces the corrupted amount.

### Recommendation
Pin the validated decimals relationship at registration time (store the local asset's decimals in `Precisions`/a new storage item alongside `erc_decimals`, or re-validate `erc_decimals >= live_decimals` inside `send()`/`on_accept`/`on_timeout` before scaling) so drift in the underlying asset's metadata cannot silently bypass the invariant. Additionally, make `convert_to_erc20`/`convert_to_balance` return an error when `local_decimals > erc_decimals` rather than silently clamping via `saturating_sub`.

### Proof of Concept
1. Governance registers asset `A` with `local_decimals = 6` and EVM `erc_decimals = 6` for chain `EVM-X` — passes `ensure!(config.decimals >= local_decimals, ...)`.
2. The asset's metadata admin (not the token gateway's `CreateOrigin`) later calls the underlying `Assets` pallet's metadata-update path to raise `A`'s decimals to `18`.
3. Attacker calls `send(asset_id = A, destination = EVM-X, amount = 1_000_000_000_000_000_000)` (1 whole token at the new 18-decimal precision), locking/burning only 1 real token's worth locally.
4. `convert_to_erc20(1e18, erc_decimals=6, local_decimals=18)` computes `10u128.pow(6.saturating_sub(18)) = 10^0 = 1`, so `erc20_amount = 1e18` is placed unscaled into `message.amount`.
5. On `EVM-X`, `HyperFungibleToken.onAccept` mints `1e18` raw units to the beneficiary, which at the contract's real 6-decimal precision equals `10^12` tokens — a 10^12x inflation relative to what was escrowed.

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

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-312)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({
            from: message.from,
            to: beneficiary,
            source: string(request.source),
            amount: message.amount
        });
    }
```
