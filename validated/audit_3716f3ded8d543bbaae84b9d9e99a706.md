## Analysis

Tests confirm the intended scaling direction is `erc_decimals >= local_decimals` (e.g., `convert_to_erc20(amount, 18, 10)` in every test). Nothing in `register_token`/`update_token` enforces this ordering, and the local asset's decimals come from `pallet-assets` metadata (per-asset, settable independently of any given destination's registered `erc_decimals`). Because `convert_to_balance`/`convert_to_erc20` use `saturating_sub` for the exponent, the case `erc_decimals < local_decimals` silently collapses the scale factor to `10^0 = 1` instead of dividing down — an unguarded decimal-direction assumption exactly like the `StableOracleDAI` bug (assumed one decimal convention, got a different one, and the code silently mis-scales rather than erroring).

### Title
Cross-chain amount corruption when a registered destination's `erc_decimals` is lower than the local asset's decimals - (`modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`convert_to_erc20`/`convert_to_balance` compute their scaling exponent with `erc_decimals.saturating_sub(local_decimals)`, silently assuming `erc_decimals >= local_decimals` always holds. When a token is registered for a destination chain whose EVM-side decimals are lower than the local asset's decimals, the exponent clamps to 0 and the amount is forwarded **unscaled**, producing a cross-chain amount that is orders of magnitude larger than the value actually escrowed/burned.

### Finding Description
`send()` computes the outbound message amount as: [1](#0-0) 

using `convert_to_erc20`: [2](#0-1) 

which multiplies by `10^(erc_decimals.saturating_sub(local_decimals))`. `erc_decimals` is `Precisions::<T>::get(asset_id, destination)`, set independently per `(asset, destination chain)` via `register_token`/`update_token`: [3](#0-2) 

`local_decimals` is fetched from the asset's own metadata via `fungibles::metadata::Inspect::decimals`, which is a property of the asset itself, not tied to any particular destination: [4](#0-3) 

If a token is bridged with local decimals (e.g. 18) that exceed the `erc_decimals` configured for a specific destination (e.g. 6, matching a real 6-decimal stablecoin contract on that chain — a completely ordinary configuration), `saturating_sub` returns 0, so `convert_to_erc20` returns `amount` unchanged instead of dividing by `10^12`. The message dispatched to the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract therefore carries an amount that is `10^(local_decimals-erc_decimals)` times larger than intended, which the destination contract will mint/unlock at face value in its own (lower-decimal) unit.

The same unguarded exponent is used symmetrically in `on_accept` (mint) and `on_timeout` (refund) via `convert_to_balance`: [5](#0-4) [6](#0-5) 

so the inbound direction (receiving from an EVM chain whose ERC20 decimals are lower than local decimals) under-credits by the same missing factor — the two paths are mirror images of the same missing-scale bug, one inflating value leaving the chain, the other destroying value arriving.

### Impact Explanation
Any signed account can call the public `send` extrinsic once an asset/destination pair with this decimal mismatch exists. Because `local_decimals` is intrinsic to the asset (fixed at asset creation, e.g. an 18-decimal bridged representation) and `erc_decimals` is set per destination to match that specific chain's real contract decimals, this is a normal, unprivileged, non-malicious configuration — not something requiring a compromised admin or relayer. The result is that a user escrowing/burning a small amount of the local asset on the source chain causes the destination `HyperFungibleToken` contract to mint/unlock an amount inflated by `10^(local_decimals - erc_decimals)`, i.e. unauthorized minting / fund creation out of thin air on the destination chain, directly matching the bounty's "unauthorized transaction/execution" and "false state acceptance leading to fund loss" categories. Conversely, transfers in the other direction (`on_accept`/`on_timeout`) silently destroy value for the beneficiary.

### Likelihood Explanation
The trigger condition — a bridged asset whose local decimals exceed the decimals registered for one particular destination chain — is a realistic and even common cross-chain scenario (e.g. an 18-decimal token bridged from Ethereum being also listed on a chain using 6-decimal stablecoin conventions, or a Polkadot asset with `Decimals=12` paired with a 6-decimal EVM wrapped token). No attacker collusion with governance, relayers, or provers is needed once such a registration exists; the exploit path is simply calling the public `send` extrinsic.

### Recommendation
Replace the `saturating_sub`-based scaling with an explicit, direction-aware conversion that handles both `erc_decimals > local_decimals` (multiply) and `erc_decimals < local_decimals` (divide) correctly, e.g.:
```rust
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    if erc_decimals >= local_decimals {
        U256::from(value) * U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        U256::from(value) / U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    }
}
```
with the mirrored fix in `convert_to_balance`, plus a `register_token`/`update_token` sanity check (or explicit tests) covering `erc_decimals < local_decimals`.

### Proof of Concept
1. `register_token` an asset with `local_decimals = 18` (e.g. via `pallet-assets` metadata) and configure `Precisions` for `StateMachine::Evm(X)` with `decimals = 6` (mirroring a real 6-decimal ERC20 on that chain) — a legitimate, expected registration.
2. Call `HyperFungibleToken::send` with `amount = 1_000_000_000_000_000_000` (1 whole token at 18 decimals) to `StateMachine::Evm(X)`.
3. Inside `send`, `erc_decimals=6`, `local_decimals=18` ⇒ `erc_decimals.saturating_sub(local_decimals) = 0` ⇒ `convert_to_erc20` returns `1_000_000_000_000_000_000` unchanged (instead of `1_000_000` as a properly-scaled 6-decimal amount).
4. The dispatched `Message.amount` field therefore encodes `1e18` instead of `1e6`; when delivered, the destination `HyperFungibleToken` contract mints/unlocks `1e18` raw units of its 6-decimal token — `1,000,000,000,000` times the value that was actually escrowed on the source chain.
5. `should_register_and_update_token` in the test suite demonstrates `Precisions` can be freely set to `6` for a destination independent of the asset's own decimals, confirming no invariant currently prevents this mismatch: [7](#0-6)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L285-295)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L54-59)
```rust
/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

**File:** modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs (L190-230)
```rust
#[test]
fn should_register_and_update_token() {
	use pallet_hyper_fungible_token::types::{ChainConfig, TokenRegistration, TokenUpdate};
	use std::collections::BTreeMap;

	new_test_ext().execute_with(|| {
		let asset_id: H256 = sp_io::hashing::keccak_256(b"NEW_TOKEN").into();
		let contract = vec![0xEEu8; 20];

		let mut chains = BTreeMap::new();
		chains.insert(
			StateMachine::Evm(42),
			ChainConfig { token_contract: H160::from_slice(&contract), decimals: 6 },
		);

		let reg = TokenRegistration { local_id: asset_id, native: false, chains };

		HyperFungibleToken::register_token(RuntimeOrigin::signed(ALICE), reg).unwrap();

		// Verify storage
		assert_eq!(
			pallet_hyper_fungible_token::TokenContracts::<Test>::get(
				StateMachine::Evm(42),
				asset_id
			)
			.unwrap(),
			contract
		);
		assert_eq!(
			pallet_hyper_fungible_token::ContractToAsset::<Test>::get(
				StateMachine::Evm(42),
				&contract
			)
			.unwrap(),
			asset_id
		);
		assert_eq!(
			pallet_hyper_fungible_token::Precisions::<Test>::get(asset_id, StateMachine::Evm(42))
				.unwrap(),
			6
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
