## Analysis

The external report's core broken invariant: a decimal-difference conversion helper only handles one direction of the decimals delta and silently produces the wrong (drastically wrong) scaled value instead of reverting, because it never checks which decimal count is larger before applying a single scale operation.

The exact local analog is the ERC20 ⇄ local-balance amount conversion used by `hyper-fungible-token` pallet for every cross-chain `send`, `on_accept`, and `on_timeout` refund. [1](#0-0) 

### Title
Hyper-Fungible-Token decimal-scaling helpers mint/refund the wrong amount when `erc_decimals < local_decimals` - (`modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`convert_to_erc20` and `convert_to_balance` scale amounts between a local asset's decimals and a remote ERC20 contract's decimals using `erc_decimals.saturating_sub(local_decimals)` (and the reverse) as the exponent for a single multiplication or division. `saturating_sub` clamps to `0` whenever the subtrahend is larger, silently turning the scale factor into `10^0 = 1` instead of scaling in the opposite direction. This means whenever the local asset has *more* decimals than the paired remote ERC20 token (a very common real-world case — e.g. a pallet asset registered with 18 decimals paired with a 6-decimal USDC-style ERC20 on the EVM side), amounts sent or received are off by exactly `10^(decimal_difference)`. [2](#0-1) 

### Finding Description
`send()` computes the outbound ERC20 amount as:
```
erc20_amount = convert_to_erc20(amount, erc_decimals, decimals)
             = amount * 10^(erc_decimals.saturating_sub(decimals))
``` [3](#0-2) 

If `erc_decimals < decimals` (local asset has more decimals than the destination ERC20 contract, e.g. local=18, remote=6), `saturating_sub` returns `0`, so the multiplier collapses to `1` — no down-scaling occurs. The raw local-balance number (already expressed with 18 decimals of precision) is transmitted verbatim as `message.amount` and interpreted by the destination as if it were already expressed in the remote's 6-decimal precision. This inflates the minted amount on the destination by `10^(18-6) = 10^12` relative to what was actually escrowed/burned on the source chain.

Symmetrically, on the receiving side, `on_accept` and `on_timeout` compute:
```
amount = convert_to_balance(value, erc_decimals, local_decimals)
       = value / 10^(erc_decimals.saturating_sub(local_decimals))
``` [4](#0-3) [5](#0-4) 

If `erc_decimals < local_decimals`, the divisor again collapses to `1`, so `value` (already at the remote's lower-decimals precision) is minted/transferred to the beneficiary as if it were already expressed in the local asset's higher-decimals precision — this instead massively **under-pays** the beneficiary (or under-refunds on timeout), because the raw small-decimal number is far smaller than the correct value once expressed at higher local precision.

Neither `convert_to_erc20` nor `convert_to_balance` checks the sign of `erc_decimals - local_decimals`; both assume the exponent argument order always yields the correct scaling direction, and `saturating_sub` masks the case where it doesn't, producing silently wrong values instead of an error. This exactly mirrors the reported `ZapMathLib.computeSharesToTwoCrypto` flaw: the conversion never accounts for the actual relationship between two independently-configured decimal counts, only for one direction of that relationship.

### Impact Explanation
**High** — this hits the "unauthorized transaction/execution" / "false state acceptance" / "loss of funds" categories directly:
- Outbound (`send`) path: when the local asset has more decimals than the destination ERC20 token's registered `Precisions` value, any unprivileged user calling `send` causes the destination chain to mint/unlock an amount inflated by `10^(decimals_difference)` relative to what was actually escrowed/burned on the source chain — i.e., value is created out of thin air on the destination chain, which is unauthorized minting/fund creation from a completely normal, permissionless call path.
- Inbound (`on_accept`)/timeout path: the reverse decimal relationship causes beneficiaries or refunded senders to receive a fraction (`10^-(decimals_difference)`) of the correct amount, i.e., silent, permanent loss of bridged funds.

Both are triggered purely through the pallet's standard public `send` extrinsic and the standard ISMP `on_accept`/`on_timeout` callbacks — no malicious relayer, prover, or governance actor is required, only a legitimate (and common) decimal mismatch between the local asset and its cross-chain ERC20 counterpart.

### Likelihood Explanation
**High** — decimal mismatches between chains are the norm rather than the exception (Substrate assets commonly use 10, 12, or 18 decimals; ERC20 stablecoins commonly use 6). Any token pairing registered via `Precisions` with `local_decimals > erc_decimals` (send direction) or `erc_decimals > local_decimals` reversed (receive direction) triggers this on every single transfer, not just an edge case.

### Recommendation
Replace the one-directional `saturating_sub` scaling with a signed comparison that scales in the correct direction regardless of which side has more decimals:
```rust
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    if erc_decimals >= local_decimals {
        U256::from(value) * U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        U256::from(value) / U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    }
}

pub fn convert_to_balance<B: core::str::FromStr>(
    value: U256, erc_decimals: u8, local_decimals: u8,
) -> Result<B, B::Err> {
    let scaled = if erc_decimals >= local_decimals {
        value / U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        value * U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    };
    scaled.to_string().parse::<B>()
}
```

### Proof of Concept
1. Register a local asset with 18 decimals and pair it (via `Precisions`) with a remote EVM ERC20 contract declared with 6 decimals (`erc_decimals = 6`, `local_decimals = 18`).
2. Attacker calls `send` with `amount = 1_000_000_000_000` (i.e., 0.000001 of a full 18-decimal unit, effectively dust).
3. `convert_to_erc20(amount, 6, 18)` computes exponent `= 6.saturating_sub(18) = 0`, so `erc20_amount = amount = 1_000_000_000_000` verbatim.
4. The destination EVM contract, expecting `erc20_amount` already scaled to 6 decimals, mints/releases `1_000_000_000_000 / 10^6 = 1_000_000` full tokens to the attacker's beneficiary — despite the attacker only having escrowed dust on the source chain, demonstrating unauthorized value creation. [6](#0-5)

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-296)
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
