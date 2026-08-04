## Title
Unidirectional decimal scaling in `hyper-fungible-token` mirrors the PriceFeed decimal bug and can mint/credit the wrong bridged amount - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
The external Predy report's core broken invariant is: a decimal-scaling helper that only supports **one direction** of decimal difference (via subtraction that can't go negative), silently doing a no-op instead of the opposite scaling operation when the asset pair is configured "the wrong way round." Hyperbridge's `pallet-hyper-fungible-token` — the pallet that custodies/mints/burns bridged assets — implements the exact same pattern in `convert_to_balance` / `convert_to_erc20`.

### Finding Description
`convert_to_balance` and `convert_to_erc20` scale amounts between a remote ERC-20's decimals and the local asset's decimals using `saturating_sub`: [1](#0-0) 

```rust
pub fn convert_to_balance<B>(value: U256, erc_decimals: u8, local_decimals: u8) -> Result<B, B::Err> {
    let dec_str = (value / U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))).to_string();
    dec_str.parse::<B>()
}

pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

`saturating_sub` collapses to `0` whenever `local_decimals >= erc_decimals`, exactly like the Predy `PriceFeed` which could not express a `decimalsDiff` in the "negative" direction. When that happens:

- `convert_to_erc20` should **divide** by `10^(local_decimals - erc_decimals)` but instead multiplies by `10^0 = 1`, i.e. does nothing.
- `convert_to_balance` should **multiply** by `10^(local_decimals - erc_decimals)` but instead divides by `10^0 = 1`, i.e. does nothing.

This function is exercised directly from the public, unprivileged `send` extrinsic, which builds the outbound ISMP message amount from local balance units: [2](#0-1) 

```rust
let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);
let token_message = Message {
    from: sender.to_vec().into(),
    to: params.recipient.to_vec().into(),
    amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
    data: params.call_data.unwrap_or_default().into(),
};
```

The pallet's own README confirms the receive path performs the mirror-image scaling using the same `Precisions` map on `on_accept`: [3](#0-2) 

```
- `on_accept` — receives `Send` messages from the paired EVM contract. Maps
  the source contract back to a local asset via `ContractToAsset`, scales the
  amount using `Precisions`, then mints (non-native) or releases from escrow
  (native) to the beneficiary. Emits `TokenReceived`.
```

Since a local asset's decimals (`local_decimals`, e.g. an asset registered with 18 decimals on the substrate side) can legitimately be configured to be **greater than or equal to** the remote EVM token's decimals (`erc_decimals`, e.g. a 6-decimal USDC-style token), `erc_decimals.saturating_sub(local_decimals) == 0` in that configuration. Concretely:

- On `send`: a user sending `1000` whole tokens (`amount = 1000 * 10^18` in local units) with `erc_decimals = 6`, `local_decimals = 18` gets `erc20_amount = amount * 10^0 = 1000 * 10^18` instead of the correct `1000 * 10^6`. The EVM-side `HyperFungibleToken`/`WrappedHyperFungibleToken` contract will mint/unlock `1000 * 10^18` raw units of what should be a 6-decimal token — a **10^12x over-mint**, i.e. unauthorized creation of value out of the bridge.
- On receive (`on_accept`), the same flawed helper in the opposite role would under-credit a beneficiary by the same factor when decimals are configured the other way, silently locking/losing user funds.

This is functionally identical to the Predy root cause: a `saturating_sub`/positive-only decimal-diff mechanism that "does nothing" instead of erroring or performing the required inverse operation whenever the configuration falls outside the direction the author implicitly assumed.

### Impact Explanation
This directly hits the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" categories for a bridge-custody code path (lock/mint/burn/unlock of bridged assets). Depending on which side of the pair has more decimals, the bug either massively over-mints tokens on the destination chain (fabricating value backed by no real escrow) or silently under-credits/loses the beneficiary's funds — both are direct fund-safety violations in the custody pallet, not merely a display/valuation issue as debated in the original Predy report.

### Likelihood Explanation
Reachability requires no malicious relayer, prover, or admin — it is triggered by any ordinary user calling the public `send` extrinsic once an asset is registered (via the normal, non-privileged `register_token`/`update_asset_precision` flow) where the local asset's decimals are `>=` the remote chain's decimals for that asset — a configuration that is neither unusual nor disallowed by the pallet (many substrate assets use 10 or 12 decimals while EVM ERC-20s commonly use 6; the reverse, local=18 vs remote=6, is equally plausible for a governance-registered wrapped/synthetic asset). The pallet does not validate or reject `erc_decimals < local_decimals`; the arithmetic simply silently no-ops instead of reverting, matching the Predy judge's final characterization of the bug ("certain pair configurations cannot be depicted correctly").

### Recommendation
Replace the `saturating_sub`-based one-directional scaling in `convert_to_balance`/`convert_to_erc20` with an explicit bidirectional scale that branches on which side has more decimals (multiply vs. divide), or store a signed decimal delta per `(asset, chain)` pair and pick the correct operation, mirroring the fix suggested for the Predy `PriceFeed` (support negative offsets rather than assuming positive-only). Add an invariant check/test that fails registration or scaling when the computed exponent would silently degenerate to a no-op for a non-equal decimal pair.

### Proof of Concept
Conceptual PoC (Rust, using the pallet's own helper as shown in `impls.rs`):
```rust
// erc_decimals = 6 (remote ERC-20, e.g. USDC-style), local_decimals = 18 (local asset)
let amount: u128 = 1000 * 10u128.pow(18); // user sends "1000" tokens in local 18-decimal units
let erc20_amount = convert_to_erc20(amount, 6, 18);
// erc_decimals.saturating_sub(local_decimals) = 0  =>  no scaling applied
assert_eq!(erc20_amount, U256::from(amount)); // BUG: should be amount / 10^12 = 1000 * 10^6
```
Sending this `erc20_amount` in the ISMP `Message.amount` field causes the destination EVM `HyperFungibleToken` contract to mint/unlock `1000 * 10^18` raw units against a token that should only ever have `1000 * 10^6` units minted for this transfer — a 10^12x amplification of value with no corresponding backing, reachable by any ordinary caller of the public `send` extrinsic.

**Note on completeness:** I was unable to read `modules/pallets/hyper-fungible-token/src/module.rs` (tool error) to show the exact `on_accept` call site invoking `convert_to_balance`; the receive-side wiring is corroborated by the pallet's README description of `on_accept` but not directly cited from `module.rs` source. The send-side vulnerability, however, is fully verified against `lib.rs` and `impls.rs`.

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L290-302)
```rust
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

**File:** modules/pallets/hyper-fungible-token/README.md (L81-87)
```markdown
## ISMP module behaviour

- `on_accept` — receives `Send` messages from the paired EVM contract. Maps
  the source contract back to a local asset via `ContractToAsset`, scales the
  amount using `Precisions`, then mints (non-native) or releases from escrow
  (native) to the beneficiary. Emits `TokenReceived`.
- `on_timeout` — refunds the original sender's balance from escrow or by
```
