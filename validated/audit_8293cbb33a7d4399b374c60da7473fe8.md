## Title
Decimal-conversion helpers `convert_to_erc20`/`convert_to_balance` silently skip scaling when source decimals exceed target decimals, allowing amount inflation across the bridge - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

## Summary
The `hyper-fungible-token` pallet bridges assets between a Substrate chain and EVM `HyperFungibleToken`/`WrappedHyperFungibleToken` contracts, and must translate amounts between the local asset's decimal precision and the ERC20 token's decimal precision on the destination chain. This is done via `convert_to_erc20` (outbound, `send`) and `convert_to_balance` (inbound, `on_accept`/`on_timeout`) using `erc_decimals.saturating_sub(local_decimals)` (or the inverse) as the scaling exponent. `saturating_sub` clamps to `0` instead of allowing the correct exponent to be negative, so whenever the "from" side has *more* decimals than the "to" side, the required scale-down never happens and the raw integer is forwarded unchanged, producing a wildly wrong (inflated) amount on the counter-chain.

## Finding Description
`convert_to_erc20` computes the ERC20-side amount as:
```
U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals)))
``` [1](#0-0) 

This is only correct when `erc_decimals >= local_decimals` (scale up). When the *local* (source) asset has more decimal places than the destination's registered ERC20 `erc_decimals` (i.e. `local_decimals > erc_decimals`), `erc_decimals.saturating_sub(local_decimals)` clamps to `0`, so the function returns `value` unscaled instead of dividing it down by `10^(local_decimals - erc_decimals)`.

This function is invoked directly in the outbound `send` extrinsic:
```
let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);
```
right after the pallet escrows/burns `params.amount` of the local asset from the caller. [2](#0-1) 

The symmetric inbound helper `convert_to_balance` has the analogous flaw in the opposite direction (no scale-*up* when `local_decimals > erc_decimals`), used in both `on_accept` and `on_timeout` to compute how much to mint/transfer to the beneficiary. [3](#0-2) [4](#0-3) 

Decimal precision per `(asset, destination chain)` is admin-configured storage (`Precisions`), and the whole point of this pallet — per the analogous Token Gateway design used elsewhere in the repo — is to support assets whose decimal precision legitimately differs across chains (the docs explicitly cite "10-decimal DOT on Polkadot vs 18-decimal DOT on Ethereum" as a normal, expected configuration). [5](#0-4) 

Because `saturating_sub` only correctly implements one direction of the conversion, any legitimately-configured asset pair where the source-side decimals exceed the destination-side decimals is exploitable: a user escrows/burns `amount` of the local asset (correct, small value), but the wire message forwards `amount` unscaled as though it were already in the destination's lower-decimal units. The destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract will mint/transfer that raw integer at its own (lower) decimal precision, which represents a vastly larger token quantity than what was actually escrowed — e.g., a 12-order-of-magnitude difference for an 18-vs-6-decimal pair.

## Impact Explanation
This is a direct amount-corruption bug in the bridge's core value-transfer path: the corrupted value is `erc20_amount` (outbound) / `amount` (inbound), computed by `convert_to_erc20`/`convert_to_balance`. It results in unauthorized minting of a vastly inflated quantity of tokens on the destination chain relative to what was locked/burned on the source — a direct value-fabrication / fund-creation bug matching "stealing or loss of funds" and "unauthorized execution" per the bounty's impact gate. No malicious relayer, prover, or admin action is required; a normal user calling the public `send` extrinsic on a legitimately-configured (but decimal-mismatched) asset triggers the flaw. The inverse case (`local_decimals > erc_decimals` on the inbound side) causes silent under-crediting/fund loss for the beneficiary, which is also a real fund-loss condition.

## Likelihood Explanation
Likelihood is Medium: it requires only that governance registers an asset pair where the source decimals exceed destination decimals (or vice versa for the inbound leg) — a configuration explicitly anticipated and documented as normal for cross-chain asset bridging (differing decimal precisions between chains, e.g. DOT 10 vs 18, or common 6-decimal stablecoins vs 18-decimal native assets). No adversarial relayer/prover/admin behavior is needed once such a pair exists; any ordinary user can trigger the exploit by simply calling `send`.

## Recommendation
Fix `convert_to_erc20` and `convert_to_balance` to correctly handle both directions of decimal difference: compare `erc_decimals` and `local_decimals` explicitly and either multiply or divide by `10^|erc_decimals - local_decimals|`, instead of relying on `saturating_sub`, which silently collapses the "wrong direction" case to a no-op scale factor of `10^0`. Add regression tests covering both `erc_decimals > local_decimals` and `erc_decimals < local_decimals` for both `send` and `on_accept`/`on_timeout` paths.

## Proof of Concept
1. Governance registers asset `X` with `local_decimals = 18` on the substrate chain, and registers `Precisions::<T>::insert(X, dest_chain, 6)` (a legitimate configuration mirroring a 6-decimal ERC20 representation of `X` on the destination EVM chain). [6](#0-5) 
2. User calls `send` with `params.amount = 1_000_000_000_000_000_000` (1 token in 18-decimal units). The pallet burns/escrows exactly `1e18` raw units from the user (correct). [7](#0-6) 
3. `convert_to_erc20(1e18, erc_decimals=6, local_decimals=18)` computes `erc_decimals.saturating_sub(local_decimals) = 6saturating_sub(18) = 0`, so the function returns `1e18 * 10^0 = 1e18` unchanged. [8](#0-7) 
4. The dispatched `Message.amount` therefore equals `1e18`, but the destination contract treats it as a 6-decimal amount (i.e., `1e18 / 1e6 = 1e12` whole tokens) rather than the correct `1e18 / 1e12 = 1e6`-scaled value (i.e., `1` whole token). The beneficiary receives roughly `10^12`× the tokens that were actually escrowed on the source chain — fabricated value with no backing collateral.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L54-59)
```rust
/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L254-256)
```rust
			let erc_decimals = Precisions::<T>::get(params.asset_id.clone(), params.destination)
				.ok_or(Error::<T>::DecimalsNotFound)?;

```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-290)
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
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L292-303)
```rust
			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

			let token_message = Message {
				from: sender.to_vec().into(),
				to: params.recipient.to_vec().into(),
				amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
				data: params.call_data.unwrap_or_default().into(),
			};

```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L82-92)
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

```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L246-256)
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

```

**File:** docs/content/developers/polkadot/token-gateway.mdx (L200-220)
```text
## Updating Asset Precision

Asset precision records the number of decimal places an asset uses on a remote chain. This allows the pallet to handle balance conversions correctly when transferring between chains with different decimal configurations (e.g. 10-decimal DOT on Polkadot vs 18-decimal DOT on Ethereum).

Dispatch `update_asset_precision` with the following parameters:

```rust lineNumbers
pub struct PrecisionUpdate<AssetId> {
    /// The local asset id
    pub asset_id: AssetId,
    /// New precisions per chain
    pub precisions: BTreeMap<StateMachine, u8>,
}
```

| Field | Description |
|-------|-------------|
| `asset_id` | The local asset ID to update precision for. |
| `precisions` | A map of `StateMachine` to `u8` specifying the new decimal precision on each chain. |

Incorrect precision values would lead to failed transfers
```
