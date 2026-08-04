Confirmed: `HyperFungibleTokenUpgradeable.send()` burns the caller's raw `params.amount` (arbitrary 18-decimal precision) with no dust rounding/rejection, and encodes that same raw amount verbatim into the `Message.amount` field of the ISMP request. [1](#0-0)  On the substrate side, `on_accept` converts this ERC20-denominated amount down to the local asset's decimals via integer division, which truncates and permanently discards any remainder ("dust") below the destination's precision. [2](#0-1) [3](#0-2) 

### Title
Truncated dust lost on cross-chain receive in `pallet-hyper-fungible-token` / `HyperFungibleTokenUpgradeable` (File: modules/pallets/hyper-fungible-token/src/module.rs, sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol)

### Summary
`pallet-hyper-fungible-token` and its EVM counterpart `HyperFungibleTokenUpgradeable`/`WrappedHyperFungibleToken` form a burn/mint bridge for a single token across chains that may have different decimal precisions (e.g. 18-decimal EVM token vs 10-decimal substrate asset). When a user sends tokens from the EVM side, the full raw `amount` is burned from the sender with no precision cleaning. [1](#0-0)  When the message reaches the substrate destination, `on_accept` calls `convert_to_balance`, which performs an integer division by `10^(erc_decimals - local_decimals)`, silently truncating any remainder. [4](#0-3)  That truncated remainder ("dust") is not credited to the beneficiary, not returned to the sender, and not tracked anywhere in pallet storage — it is simply lost. This is the same broken invariant as the referenced LayerZero-OFT dust report: the source chain moves/burns the full amount but the destination credits a lesser amount because of a decimal-precision mismatch that is not reconciled.

### Finding Description
The relevant conversion helpers are:
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

This is invoked in `on_accept`:
```rust
let amount = convert_to_balance::<...>(
    U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
    erc_decimals,
    decimals,
)...
``` [2](#0-1) 

On the EVM sending side, `send()` burns the caller's raw `params.amount` with no rounding to the destination chain's precision, and forwards that exact raw value in the outbound `Message.amount`:
```solidity
function send(SendParams calldata params) external payable whenNotPaused {
    _burn(msg.sender, params.amount);
    DispatchPost memory request = _buildDispatchPost(params);
    ...
``` [1](#0-0) 

Because `send()` never validates or rounds `params.amount` to a multiple of `10^(erc_decimals - local_decimals)`, a user can burn e.g. `1234567890123456789` wei (18 decimals) destined for a chain configured with `erc_decimals=18, local_decimals=10` (scale factor `10^8`). On `on_accept`, `convert_to_balance` computes `1234567890123456789 / 10^8 = 12345678901` (integer division), discarding the low-order `23456789` (in 18-decimal terms, dust < 1e8 wei). That dust was already burned on the EVM chain and is never minted, escrowed, refunded, or otherwise recovered on the destination — it disappears from total supply accounting entirely.

Unlike the outbound substrate→EVM path (where `convert_to_erc20` performs an exact upscale multiplication, so the corresponding `convert_to_balance` reversal in `on_timeout` is lossless by construction because `erc_decimals >= local_decimals` is enforced via `ErcDecimalsBelowLocal`) [5](#0-4) , the inbound EVM→substrate path accepts an arbitrary raw `uint256` amount from an unprivileged caller with no equivalent dust-cleaning guard before burn. This asymmetry means the loss is one-directional and fully attacker/user triggerable — any ordinary user calling `send()` on the EVM contract with a non-round amount forfeits the dust permanently, and repeated across many small transfers this can drain meaningful value out of circulation with no path to recovery (no `DustReturned`/`DustCredited` event or storage exists in the pallet).

### Impact Explanation
This matches the required impact class "unauthorized ... loss of funds": value is burned from the sender's balance on the source chain but never fully minted/credited on the destination — it is neither in the sender's balance, the beneficiary's balance, nor the pallet's escrow, violating the "moves exactly once and only to the rightful beneficiary and amount" invariant for bridged assets. Because dust is discarded rather than tracked, total cross-chain supply invariants between the EVM and substrate sides of the token silently diverge over time, and no accounting mechanism exists to reconcile or return this dust to users.

### Likelihood Explanation
High likelihood in practice: any token pair configured with differing decimals between the EVM chain and a substrate chain (a documented, expected, and common configuration per the pallet's own `Precisions`/`update_asset_precision` design) [6](#0-5)  will produce this loss for essentially every transfer amount that isn't an exact multiple of the scale factor — no special conditions, malicious actor, relayer, or governance action is required. A completely ordinary user calling the public `send()` entrypoint with a "normal-looking" 18-decimal amount will very frequently hit non-round values.

### Recommendation
Before burning/locking funds in `send()` (EVM) — and symmetrically before dispatching in the substrate `send()` extrinsic when erc_decimals could exceed local granularity in the reverse direction — round the amount down to the nearest representable unit at the destination's precision and either revert on non-zero dust or refund the dust back to the caller immediately, mirroring the LayerZero OFT `_removeDust`/`_toSD`/`_toLD` pattern. Concretely: compute `cleanedAmount = (amount / scaleFactor) * scaleFactor` prior to burning, and either `revert` if `amount != cleanedAmount` or transfer the leftover dust back to `msg.sender` before dispatch.

### Proof of Concept
1. Register an asset with `erc_decimals = 18` (EVM) and `local_decimals = 10` (substrate), scale factor `10^8`.
2. On the EVM `HyperFungibleTokenUpgradeable`, call `send({ amount: 1_234_567_890_123_456_789, ... })`. This burns the full `1_234_567_890_123_456_789` from the caller. [1](#0-0) 
3. The ISMP request is delivered to the substrate pallet; `on_accept` runs `convert_to_balance(1_234_567_890_123_456_789, 18, 10)` = `1_234_567_890_123_456_789 / 10^8` = `12_345_678_901` (local units), discarding the remainder `23_456_789` (i.e. `2_345_678_9` in 18-decimal terms, ~2.3×10⁻¹⁰ tokens per transfer, compounding across volume). [3](#0-2) [7](#0-6) 
4. Beneficiary is credited `12_345_678_901` local units; no entity anywhere holds or can claim the discarded dust — it is unrecoverably lost from total cross-chain supply.

Note: I was unable to fully trace the substrate-outbound direction's boundary check (the exact enforcement point of `ErcDecimalsBelowLocal`) beyond the error definition, so I cannot state with certainty whether an equivalent unvalidated raw-amount path exists on the substrate `send()` extrinsic side as well; the EVM→substrate direction shown above is verified directly against the code.

### Citations

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L293-300)
```text
    function send(SendParams calldata params) external payable whenNotPaused {
        _burn(msg.sender, params.amount);
        DispatchPost memory request = _buildDispatchPost(params);

        bytes32 commitment;
        if (msg.value > 0) {
            commitment = IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L219-224)
```rust
		/// Peer chain is not an EVM state machine; this pallet bridges substrate <-> EVM only
		NonEvmPeerChain,
		/// Configured ERC decimals are less than the local asset's decimals; precision conversion
		/// requires erc_decimals >= local_decimals
		ErcDecimalsBelowLocal,
	}
```

**File:** docs/content/developers/polkadot/hyper-fungible-token.mdx (L115-129)
```text
pub struct ChainConfig {
    /// The HyperFungibleToken/WrappedHyperFungibleToken module ID on the destination chain
    pub token_contract: Vec<u8>,
    /// ERC20 decimals on the destination chain
    pub decimals: u8,
}
```

| Field | Description |
|-------|-------------|
| `local_id` | The local asset ID. The type depends on your runtime's `Assets` pallet configuration. |
| `native` | Controls the custody model. `true` = tokens are escrowed (for assets originating on this chain). `false` = tokens are burned (for bridged/imported assets). |
| `chains` | A map of destination chains to their contract configuration. |
| `chains[].token_contract` | The module ID of the `HyperFungibleToken` or `WrappedHyperFungibleToken` on that chain. For EVM chains, this is the 20-byte contract address. For substrate chains, this is the 8-byte pallet ID. |
| `chains[].decimals` | The ERC20 decimal precision on that chain (typically `18` for EVM). |
```
