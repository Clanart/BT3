## Analysis

The external report's core broken invariant: **a lossy decimal-scaling division on a value that should preserve full precision, with no check that the division is exact**, causing part of the value to silently vanish.

The Hyperbridge analog is in the token-bridging precision conversion of `pallet-hyper-fungible-token`, specifically `convert_to_balance` used on the fund-crediting path (`on_accept`) and the refund path (`on_timeout`). [1](#0-0) 

### Title
Silent truncation in `convert_to_balance` permanently strands bridged funds in `pallet-hyper-fungible-token` - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
When crediting inbound cross-chain transfers, the pallet converts an ERC20-precision `U256` amount down to the local Substrate balance by floor-dividing: `value / 10^(erc_decimals - local_decimals)`. Unlike the sibling EVM contract `BandwidthManager.purchase()`, which explicitly guards against non-exact scaling with `if (total18d % scale != 0) revert PriceNotRepresentable();`, this pallet performs no remainder/modulo check before dividing. [2](#0-1) [3](#0-2) 

### Finding Description
`on_accept` decodes the inbound `Message.amount` (an EVM `uint256` chosen by the sender/relayer of the source-chain lock/burn call) and converts it to a local balance via `convert_to_balance`: [4](#0-3) 

The resulting `amount` — after floor division — is what actually moves: either transferred out of the pallet's custodial account for native/locked assets, or minted for non-native assets: [5](#0-4) 

For a native/escrowed asset, the corresponding EVM-side contract locked the *full, unrounded* `message.amount` into the escrow it represents; here the pallet only releases the floor-divided (rounded-down) amount to the beneficiary. Any remainder below the local asset's precision (`erc_decimals - local_decimals` digits of resolution) is neither refunded, credited, nor tracked anywhere — it is simply unaccounted for. The same lossy conversion is repeated identically on the timeout/refund path, so a refund after a failed delivery also strands the same remainder: [6](#0-5) 

The precision differential (`erc_decimals - local_decimals`) is governance-configured per `(asset, chain)` via `register_token`/`update_token`, and is commonly non-trivial (e.g., 18-decimal EVM token vs. 12-decimal Substrate asset ⇒ a `10^6` scale factor). Any `message.amount` that is not an exact multiple of `10^6` loses its remainder on every single inbound transfer. Because `Message.amount` is fully attacker/relayer-controlled ABI-encoded data crossing the bridge (bounded only by what the source-chain HFT/WrappedHFT contract locked/burned), an ordinary user calling the bridge with an amount that isn't a clean multiple of the scale factor causes real, permanent fund loss on every such transfer — with no revert, no event, and no path to recover the dust.

### Impact Explanation
This is a direct "loss of funds" primitive in the custody/settlement path: value locked/burned on the source chain is not fully credited on the destination chain, and the shortfall is unrecoverable (it sits unlabeled inside the pallet's custodial account for native assets, or is simply never minted for non-native assets — either way, permanently lost to the depositor and unclaimable by anyone). This matches the bounty's explicit "stealing or loss of funds" and "transaction manipulation" categories, and is a stronger analog of the H-05 precision-loss class because here the truncated component is real custodied value, not just a price oracle reading.

### Likelihood Explanation
This requires no privileged actor, malicious relayer, or compromised prover — a normal user driving the standard `send()`/lock flow from the EVM side with any amount that isn't an exact multiple of the configured scale factor triggers the loss on every affected transfer. Given decimal mismatches between EVM tokens (commonly 18) and Substrate assets (often 10 or 12), non-exact amounts are the common case, not an edge case, so the likelihood of triggering this is high and reproducible on demand.

### Recommendation
Apply the same pattern already used in `BandwidthManager.sol`: before dividing, compute the remainder and reject (or explicitly refund/queue) any amount that isn't a clean multiple of the scale factor, e.g.:
```rust
let scale = U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32));
ensure!(value % scale == U256::zero(), Error::NonRepresentableAmount);
let local_value = value / scale;
```
Alternatively, track and refund the truncated remainder rather than silently discarding it.

### Proof of Concept
1. Governance registers a token with `erc_decimals = 18` (EVM side) and the local asset has `decimals = 12` (Substrate side) — allowed since `register_token`/`update_token` only require `erc_decimals >= local_decimals`. [7](#0-6) 
2. A user calls the source-chain HyperFungibleToken/WrappedHyperFungibleToken contract to lock/burn `1_000_000_000_000_000_001` wei (18 decimals) and bridge it to Substrate.
3. `on_accept` decodes `message.amount = 1_000_000_000_000_000_001`, computes `scale = 10^(18-12) = 10^6`, and `convert_to_balance` floor-divides: `1_000_000_000_000_000_001 / 1_000_000 = 1_000_000_000_000` (local balance), discarding the trailing `1` wei-equivalent remainder with no error, event, or accounting. [3](#0-2) [8](#0-7) 
4. The beneficiary receives `1_000_000_000_000` local units while the source chain custody/burn accounted for the full `1_000_000_000_000_000_001` — the difference is permanently unaccounted for and unrecoverable. Repeating with an amount like `X * 10^6 + (10^6 - 1)` maximizes the per-transfer loss up to just under one full local unit, and this can be repeated indefinitely across transfers to accumulate stranded value in the pallet's custodial account.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L39-52)
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L74-117)
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L246-265)
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
