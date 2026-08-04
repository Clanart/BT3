## Title
`saturating_sub` decimal-scaling bug in `pallet-hyper-fungible-token` causes wrong-direction amount conversion, enabling massive over-mint/under-credit of bridged value - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
The Substrate `pallet-hyper-fungible-token` converts amounts between the local asset's decimal precision and the paired EVM contract's decimal precision using `convert_to_erc20`/`convert_to_balance`. Both functions compute the scaling exponent with `erc_decimals.saturating_sub(local_decimals)` (or vice versa), which silently clamps to `0` — meaning "no scaling" — whenever the subtraction would be negative, instead of scaling in the opposite direction. This is functionally the same failure class as the reported AlchemistV2 issue: an amount is carried across a value boundary using an implicit, unconditional 1:1 (or wrong-ratio) assumption instead of the correct conversion, letting one side of the transfer be worth many orders of magnitude more or less than what was actually escrowed/burned.

### Finding Description
`convert_to_erc20` (outbound, local → ERC20 units) and `convert_to_balance` (inbound, ERC20 → local units) are defined as: [1](#0-0) 

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

pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

Both use `saturating_sub`, which returns `0` whenever `local_decimals > erc_decimals` (for `convert_to_erc20`) or whenever `local_decimals > erc_decimals` (for `convert_to_balance`, since it also computes `erc_decimals.saturating_sub(local_decimals)`). In that case `10u128.pow(0) == 1`, so the function performs **no scaling at all**, when correct behavior requires scaling by `10^(local_decimals - erc_decimals)` in the opposite operation (divide in `convert_to_erc20`, multiply in `convert_to_balance`).

Concretely, for a token registered with `local_decimals = 18` and EVM `Precisions = 6` (a routine registration — many EVM stablecoins use 6 decimals while a Substrate `pallet-assets` asset or the native currency uses 12/18):

- **Outbound (`send`)**, called from the public extrinsic: [2](#0-1) 
`convert_to_erc20(amount, erc_decimals=6, local_decimals=18)` computes `erc_decimals.saturating_sub(local_decimals) = 0`, so `erc20_amount = amount` unscaled — instead of `amount / 10^12`. The `Message.amount` dispatched to the EVM `HyperFungibleToken`/`WrappedHyperFungibleToken` contract is **10^12 times larger** than intended.

- **Inbound (`on_accept`/`on_timeout`)**: [3](#0-2) 
`convert_to_balance(value, erc_decimals=6, local_decimals=18)` again computes exponent `0`, so the credited local balance equals the raw ERC20 `U256` value unscaled — instead of `value * 10^12`. Inbound transfers get credited **10^12 times smaller** than what was actually burned/locked on the EVM side.

Existing guards (`DecimalsNotFound`/`DecimalsNotConfigured` errors) only check that a `Precisions` entry exists — they never validate the relative ordering of `local_decimals` vs `erc_decimals`, so nothing catches or rejects this case.

### Impact Explanation
This directly matches the bounty's "transaction manipulation" / "false state acceptance" / fund-loss categories: the amount transmitted across the bridge boundary is silently corrupted by up to `10^n` (n = decimal difference), in either direction depending on which leg (`send` vs `on_accept`) is exercised:
- Outbound miscalculation can cause the destination `HyperFungibleToken` contract to mint (or the `WrappedHyperFungibleToken` to unlock) a wildly inflated amount relative to what was actually escrowed/burned on the Substrate side — an unauthorized value-creation / fund-drain against the bridge's own custody/liquidity on the EVM side.
- Inbound miscalculation causes users to receive a fraction of what they should when bridging into the Substrate side (fund loss to end users, or — combined with round-tripping — can be leveraged by an attacker to lock a tiny amount, receive a large mismatched value on one side, and repeat).

This is a public-entrypoint issue (`send` extrinsic and the message handlers invoked automatically by `pallet-ismp` on `on_accept`/`on_timeout`) reachable by any unprivileged user who bridges a token whose registered decimal precisions happen to differ in this direction — no malicious relayer, prover, or governance action is required; only a normal, legitimate token registration with `local_decimals > erc_decimals`.

### Likelihood Explanation
Likelihood is high in practice: mismatched decimal configurations across chains are the common case (EVM USDC/USDT = 6 decimals; Substrate native/asset balances frequently 10, 12, or 18 decimals), so any token registered with `Precisions` (EVM decimals) lower than the local asset's decimals will silently trigger this bug on every transfer, without any special crafting by an attacker.

### Recommendation
Replace the `saturating_sub`-based exponent computation with a signed comparison that scales in the correct direction on both branches:

```rust
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    if erc_decimals >= local_decimals {
        U256::from(value) * U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        U256::from(value) / U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    }
}

pub fn convert_to_balance<B: core::str::FromStr>(
    value: U256,
    erc_decimals: u8,
    local_decimals: u8,
) -> Result<B, B::Err> {
    let scaled = if erc_decimals >= local_decimals {
        value / U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        value * U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    };
    scaled.to_string().parse::<B>()
}
```
Add regression tests covering both `local_decimals > erc_decimals` and `local_decimals < erc_decimals` for both functions.

### Proof of Concept
1. Governance registers a token with `register_token`, setting `Precisions::<T>::insert(asset_id, StateMachine::Evm(1), 6)` (EVM side uses 6 decimals) while the local asset (or native currency via `T::Decimals`) uses 18 decimals — a realistic configuration.
2. A user calls `send(SendParams { asset_id, amount: 1_000_000_000_000_000_000 /* 1 token, 18 dec */, destination: StateMachine::Evm(1), .. })`. [4](#0-3) 
3. `convert_to_erc20(1e18, erc_decimals=6, local_decimals=18)` returns `1e18` unscaled (should return `1e18 / 10^12 = 1e6`, i.e. "1.0" in 6-decimal terms). The dispatched `Message.amount` is `1e18`, i.e. `10^12` "tokens" instead of `1`.
4. On the EVM side, `HyperFungibleToken.onAccept` mints `message.amount` (1e18 raw units at 6 decimals = 1 trillion tokens) to the beneficiary — vastly more than the single token that was actually locked/burned on the Substrate side, draining the bridge's economic backing.
5. Symmetrically, sending 1 trillion EVM-side tokens (6 decimals) back would, via `convert_to_balance(value, erc_decimals=6, local_decimals=18)`, credit the same raw numeric value with no `10^12` upscaling, crediting a comically tiny amount on the Substrate side — an under-credit that permanently strands value for the user (or lets an attacker burn on EVM and later exploit the inconsistent internal accounting across repeated round-trips).

### Citations

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-302)
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

			let token_message = Message {
				from: sender.to_vec().into(),
				to: params.recipient.to_vec().into(),
				amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
				data: params.call_data.unwrap_or_default().into(),
			};
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
