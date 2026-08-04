### Title
Precision truncation in `convert_to_balance` permanently destroys value on cross-chain receipt - ([File: modules/pallets/hyper-fungible-token/src/impls.rs])

### Summary
### Finding Description
The `hyper-fungible-token` pallet bridges assets between a Substrate chain and EVM `HyperFungibleToken`/`WrappedHyperFungibleToken` contracts, converting amounts between the local asset's decimal precision and the ERC20's decimal precision on each leg of the transfer.

On the outbound leg (`send`), the pallet enforces `config.decimals >= local_decimals` at registration time [1](#0-0) , and then scales the locally-escrowed/burned amount **up** to ERC20 precision via multiplication in `convert_to_erc20`, which is loss-free [2](#0-1) .

On the inbound leg (`on_accept`), the reverse conversion is required: an arbitrary `uint256` amount arriving from the EVM side (higher precision) must be scaled **down** to the local asset's (lower) precision using `convert_to_balance`:

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
``` [3](#0-2) 

This performs plain integer division with no check that `value` is an exact multiple of `10^(erc_decimals - local_decimals)`, and no accounting of the discarded remainder. `on_accept` calls this conversion directly on the untrusted, ISMP-delivered `message.amount` and then mints/unlocks only the truncated result to the beneficiary:

```rust
let amount = convert_to_balance::<...>(
    U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
    erc_decimals,
    decimals,
)...
``` [4](#0-3) 

The same truncating conversion is also used in `on_timeout` for refunds [5](#0-4) .

For non-native (mint/burn) assets, the EVM side burns/locks the *full* precise ERC20 amount before dispatching the ISMP message, but the destination pallet only mints `floor(amount / scale)` — the remainder `amount % scale` is discarded entirely and never minted, transferred, or tracked anywhere in pallet storage. There is no compensating credit, no dust ledger, and no way to recover it. If `amount < scale` the division truncates to zero, so the entire transferred value is silently annihilated while the source chain considers the request successfully delivered.

This is the direct local analog of the reported `StakingManager` bug: a decimal/precision mismatch between two accounting domains (HyperCore's 8-decimal format vs. 18-decimal wei there; ERC20 `erc_decimals` vs. local asset `decimals` here) causes silent truncation of value during a cross-domain transfer, with the truncated remainder permanently lost and no mechanism to reconcile it.

### Impact Explanation
This is a direct, permanent loss of bridged value:
- For non-native/mint-burn assets, the destination chain mints strictly less than what was burned on the source chain — real economic value is destroyed on every transfer whose ERC20 amount is not an exact multiple of the decimal scale factor.
- For native (locked/custody) assets, the truncated remainder stays stuck in the pallet's custody account (`Pallet::<T>::pallet_account()`) forever, unreachable by the sender, recipient, or any recovery mechanism (unlike the source, `TokenRefunded`/`TokenReceived` events only report the truncated amount actually moved).
- Because ordinary EVM users control the exact `uint256` value passed into the `HyperFungibleToken`/`WrappedHyperFungibleToken` `send`-style calls, virtually any transfer amount that isn't already scale-aligned triggers this loss — this is not an edge case requiring special conditions, it is the default behavior for most non-round amounts.
- This falls squarely under the "stealing or loss of funds" bounty category: legitimate bridge users permanently lose part of the value they bridge, with no path to recovery, and the pallet's own accounting (`TokenReceived`/`TokenRefunded` events, asset totals) silently reflects less value than was actually escrowed/burned on the source chain.

### Likelihood Explanation
High likelihood: no privileged actor, relayer misbehavior, or malicious peer is required. Any normal user teleporting an amount that is not an exact multiple of `10^(erc_decimals - local_decimals)` (e.g., 6-decimal local asset receiving from an 18-decimal EVM ERC20, a 10^12 scale factor) triggers the truncation on every single such transfer. Given typical decimal configurations (Substrate assets commonly using 6, 10, or 12 decimals vs. 18-decimal EVM ERC20s), essentially all "natural" transfer amounts will not be perfectly scale-aligned, making this the common case rather than a rare edge case.

### Recommendation
- Reject incoming messages whose `message.amount` is not an exact multiple of `10^(erc_decimals - local_decimals)` (i.e., require `value % scale == 0`), forcing senders to only transfer scale-aligned amounts, or
- Track the truncated remainder explicitly in pallet storage (per asset/chain) as recoverable dust, and provide an extrinsic to sweep/redeem it, similar to the recommendation in the analogous report, or
- Round down consistently on the *sending* side too (require the EVM contract to reject/adjust non-aligned amounts before burn/lock) so that the amount escrowed on the source chain always exactly matches what will be credited on the destination, eliminating any residual value that can be lost or stranded.

### Proof of Concept
1. Register an asset with `local_decimals = 6` and `erc_decimals = 18` for an EVM destination via `register_token` (satisfies `erc_decimals >= local_decimals`) [1](#0-0) .
2. On the EVM `HyperFungibleToken` contract, a user calls the token's send/transfer entrypoint with an arbitrary `amount = 1_000_000_000_000_000_001` wei (18 decimals), i.e., 1 unit plus 1 wei — not a multiple of `10^12`. The EVM contract burns/locks the full amount and dispatches an ISMP `PostRequest` with `Message.amount = 1_000_000_000_000_000_001`.
3. Hyperbridge relays the request; the pallet's `on_accept` runs:
   ```rust
   let amount = convert_to_balance(U256::from(1_000_000_000_000_000_001u128), 18, 6)?;
   // = 1_000_000_000_000_000_001 / 10^12 = 1_000_000  (floor)
   ``` [6](#0-5) 
4. The pallet mints/unlocks exactly `1_000_000` local units to the beneficiary — the `1` wei-scale remainder (representing `0.000000000001` of a token, but nonzero absolute ERC20 value that was actually burned/locked on EVM) is discarded with no storage entry, no event capturing it, and no recovery mechanism.
5. Repeating this at scale (e.g., batching many small non-aligned transfers, or any real-world usage where users don't manually round to `10^12`-aligned amounts) accumulates a permanent, protocol-wide loss of bridged value with no corresponding compensating credit anywhere in the system.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L352-355)
```rust
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-52)
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L248-255)
```rust
				let amount = convert_to_balance::<
					<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
				>(
					U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
					erc_decimals,
					decimals,
				)
				.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;
```
