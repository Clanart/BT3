### Title
Decimal-scaling helpers silently drop scaling when EVM decimals < local decimals, causing cross-chain amount amplification/under-crediting in `pallet-hyper-fungible-token` - (File: modules/pallets/hyper-fungible-token/src/impls.rs)

### Summary
`pallet-hyper-fungible-token` bridges tokens between a Substrate chain and paired `HyperFungibleToken`/`WrappedHyperFungibleToken` EVM contracts, using `Precisions` storage to convert amounts between local decimals and the remote EVM token's decimals at the message boundary. The scaling helpers `convert_to_erc20` and `convert_to_balance` use `erc_decimals.saturating_sub(local_decimals)` / vice versa to compute a power-of-ten multiplier. `saturating_sub` silently clamps to `0` whenever the local asset has *more* decimals than the configured EVM-side decimals, which skips the required scaling instead of applying it in the opposite direction. This is the same root class of bug as the external report (decimal-precision handling across chains not correctly accounted for at a chain boundary), but here it corrupts the actual bridged amount rather than merely tripping a slippage check.

### Finding Description
The conversion helpers are: [1](#0-0) 

```rust
pub fn convert_to_balance<B: core::str::FromStr>(
    value: U256, erc_decimals: u8, local_decimals: u8,
) -> Result<B, B::Err> {
    let dec_str = (value / U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))).to_string();
    dec_str.parse::<B>()
}

pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

These are called in `send()` (outgoing) and in `on_accept`/`on_timeout` (incoming) of the `IsmpModule` implementation: [2](#0-1) [3](#0-2) 

Both helpers only handle the case `erc_decimals >= local_decimals` correctly. When `local_decimals > erc_decimals` (e.g. a Substrate asset registered with 18 decimals paired to an EVM ERC-20 with 6 decimals — a legitimate, unvalidated configuration since `register_token`/`update_token` never assert `erc_decimals >= local_decimals`), `erc_decimals.saturating_sub(local_decimals)` clamps to `0`, so the multiplier becomes `10^0 = 1` and **no scaling is applied at all**, instead of the correct division/multiplication by `10^(local_decimals - erc_decimals)`.

Consequences:
- In `send()`: `erc20_amount = convert_to_erc20(amount, erc_decimals, decimals)` should scale the locally-denominated `amount` *down* to the EVM token's coarser precision, but instead forwards the raw local-precision integer unchanged. The destination `HyperFungibleToken` contract interprets this raw value directly in its own (fewer) decimals, so the recipient receives/mints an amount inflated by `10^(local_decimals - erc_decimals)` relative to what the sender actually escrowed/burned.
- In `on_accept`/`on_timeout`: `convert_to_balance` should scale the EVM-denominated `message.amount` *up* to local precision, but instead passes it through unscaled, so `mint_into`/`transfer` credits the beneficiary `10^(local_decimals - erc_decimals)` times *less* than the value that was actually escrowed/burned on the source side.

No slippage/minimum check exists in this pallet to catch either direction — unlike the LayerZero OFT `minAmountLD` check that reverts on truncation, here the mis-scaled amount is silently accepted and moved.

### Impact Explanation
This is a direct "false amount acceptance" / fund-loss and unauthorized-value-creation bug in bridge custody:
- Outgoing direction (`send`): an ordinary signed user can trigger minting/release of a wildly inflated amount on the destination EVM chain relative to what was actually escrowed/burned on the Substrate side — i.e., value creation out of thin air on the destination chain, directly draining the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract's backing or over-minting a wrapped asset.
- Incoming direction (`on_accept`/`on_timeout`): funds genuinely locked/burned on the EVM side are under-credited to the Substrate beneficiary, permanently losing the difference (stuck/lost funds), since the pallet has no accounting reconciliation beyond this single conversion.

Both cases satisfy the bounty's "stealing or loss of funds" / "false proof or state acceptance" criteria and require no malicious relayer, prover, or governance actor — it is triggered by a normal `send()` call or a normal incoming message once a token pair is configured with `local_decimals > erc_decimals`.

### Likelihood Explanation
Likelihood depends on token configuration: any asset pair where the Substrate-side asset decimals exceed the paired EVM contract's decimals (a realistic scenario, e.g. an 18-decimal native/parachain asset bridged to a 6-decimal EVM stablecoin representation) triggers the bug on every transfer in that direction, deterministically and with no attacker sophistication required — this is not a probabilistic dust/rounding issue like the external report but a total loss of scaling. `register_token`/`update_token` do not validate that `erc_decimals >= local_decimals`, so nothing in the codebase prevents this configuration from being set up in the first place.

### Recommendation
Fix both helpers to handle the decimals difference symmetrically in both directions instead of using `saturating_sub`, e.g.:

```rust
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    if erc_decimals >= local_decimals {
        U256::from(value) * U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        U256::from(value) / U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    }
}
```
and the mirror image for `convert_to_balance`. Additionally, add a governance-time invariant check (or explicit documented support) rather than silently truncating, and consider validating decimal configuration at `register_token`/`update_token` time.

### Proof of Concept
Given `local_decimals = 18`, `erc_decimals = 6` (configured via `Precisions`), and a user calling `send()` with `amount = 1_000_000_000_000_000_000` (1 token, 18-decimal local units):

1. `send()` computes `erc20_amount = convert_to_erc20(1e18, erc_decimals=6, local_decimals=18)`.
2. `erc_decimals.saturating_sub(local_decimals) = 6u8.saturating_sub(18) = 0`, so `erc20_amount = 1e18 * 10^0 = 1e18`.
3. The dispatched `Message.amount` field carries `1e18`, which the destination `HyperFungibleToken` EVM contract (6-decimal token) treats as a raw token amount — i.e., `1e18 / 1e6 = 1,000,000` whole tokens are released/minted to the recipient, instead of the intended `1` token (a `10^12`-fold amplification), even though only 1 token's worth (1e18 local units) was escrowed/burned on the Substrate side.

This can be confirmed by extending the existing pallet test `should_send_asset_correctly`/`should_receive_asset_correctly` in `modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs` with a `Precisions` entry where `erc_decimals < local (Decimals::get())`, and observing the mismatched `Message.amount` produced by `convert_to_erc20` versus the actual escrowed/burned local balance. [4](#0-3)

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L292-296)
```rust
			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L82-91)
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

**File:** modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs (L79-90)
```rust
				let msg = Message {
					from: alloy_primitives::Bytes::from(vec![0x11u8; 20]),
					to: alloy_primitives::Bytes::from(ALICE.as_slice().to_vec()),
					amount: {
						let bytes = convert_to_erc20(SEND_AMOUNT, 18, 10).to_big_endian();
						alloy_primitives::U256::from_be_bytes(bytes)
					},
					data: alloy_primitives::Bytes::default(),
				};
				Message::abi_encode(&msg)
			},
		};
```
