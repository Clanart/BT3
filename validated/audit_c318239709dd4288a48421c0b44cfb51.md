## Analog Found: Decimal Truncation Silently Burns Dust When Crediting Cross-Chain Transfers in `hyper-fungible-token`

### Title
Decimal-scaling truncation in `convert_to_balance` permanently destroys token dust on every EVM→Substrate transfer — ([File: modules/pallets/hyper-fungible-token/src/impls.rs])

### Summary
The `hyper-fungible-token` pallet's `on_accept` (and `on_timeout`) handlers convert an incoming ERC20-denominated `U256` amount into the local substrate `Balance` type via `convert_to_balance`, which performs a **flooring integer division** by `10^(erc_decimals - local_decimals)`. Any incoming amount that is not an exact multiple of that scaling factor has its remainder silently discarded — never credited to the beneficiary, never refunded, and never tracked anywhere. This is the direct Hyperbridge analog of the reported `bridgeToL1()` decimal-truncation bug: non-round amounts are effectively burned because the code does not sanitize/round the amount before initiating the transfer, nor account for the truncated remainder afterward.

### Finding Description
`convert_to_balance` performs the down-scaling with plain integer division, which truncates toward zero (floors): [1](#0-0) 

This function is invoked in `on_accept`, the entrypoint that credits a beneficiary when a message arrives from an EVM chain: [2](#0-1) 

The `decimals`/`erc_decimals` pair is only constrained at registration time to satisfy `erc_decimals >= local_decimals` (`Error::ErcDecimalsBelowLocal`): [3](#0-2) 

That check only bounds the *decimals configuration*; it does nothing to guarantee that any given `message.amount` (an arbitrary `U256` decoded from the incoming ISMP request body) is an exact multiple of `10^(erc_decimals - local_decimals)`. Because the message body is attacker/user-supplied application data carried across an ordinary honest post request (no malicious relayer, prover, or admin needed — any EVM-side contract or user simply sending a "non-round" raw amount triggers it), the pallet will:
1. Compute `amount = convert_to_balance(message.amount, erc_decimals, decimals)`, silently flooring.
2. Mint/transfer only the floored `amount` to the beneficiary.
3. Never track, refund, or emit the truncated remainder anywhere.

The same lossy conversion is reused in the timeout/refund path: [4](#0-3) 

For the `send → timeout` round trip specifically, the outbound `amount → erc20_amount` conversion multiplies by the same power of ten (`convert_to_erc20`), so refunds of pallet-originated messages are lossless *as long as the precision configuration hasn't changed between dispatch and timeout*. The systemic loss is on the **inbound crediting path** (`on_accept`), where the ERC20-denominated `message.amount` is externally supplied and has no guarantee of being a clean multiple of the configured scaling factor.

### Impact Explanation
Every EVM→Substrate transfer whose ERC20 amount is not an exact multiple of `10^(erc_decimals - local_decimals)` permanently loses the truncated remainder. Funds are neither credited to the beneficiary nor recoverable — they are effectively burned, exactly matching the reported bug class ("small amounts burned when bridging" due to unsanitized decimal truncation). This is a genuine, repeatable loss-of-funds condition reachable by any ordinary user of the bridge, not requiring a malicious peer/relayer/prover/admin.

### Likelihood Explanation
Likelihood is high in practice: any token whose EVM-side unit granularity doesn't align perfectly with the configured `erc_decimals - local_decimals` power-of-ten split (e.g., fee-on-transfer effects, rounding upstream in the sending contract, or a user simply supplying an odd raw amount) will trigger truncation on essentially every non-round transfer. No special conditions or privileged actors are required — it's a property of the arithmetic itself, so it will recur continuously in production usage.

### Recommendation
Before crediting, either:
- Reject the message when `message.amount % 10^(erc_decimals - local_decimals) != 0` (returning an error so the request errors out / gets retried/refunded at the source instead of silently losing value), or
- Track the truncated remainder in an on-chain "dust" ledger per asset/beneficiary that can be swept or claimed later, mirroring the recommended fix of rounding down *before* dispatch and accounting for the difference rather than discarding it silently.

### Proof of Concept
1. Register an asset with `local_decimals = 6` and `erc_decimals = 18` (scaling factor `10^12`), satisfying the `ErcDecimalsBelowLocal` check.
2. From the EVM side, construct (or have any user naturally produce) a message with `amount = 1_000_000_000_001` wei-equivalent (i.e., `10^12 + 1`), which is **not** a multiple of `10^12`.
3. Deliver this as a normal ISMP post request to the pallet; `on_accept` executes `convert_to_balance(10^12 + 1, 18, 6)` → integer division yields `1`, discarding the `1` extra unit.
4. The beneficiary is credited `1` local unit corresponding to `10^12` wei, while `1` wei of value that was escrowed/burned on the EVM side is permanently lost — never credited, never refunded, unrecoverable. [5](#0-4) [6](#0-5)

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L350-355)
```rust
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
```
