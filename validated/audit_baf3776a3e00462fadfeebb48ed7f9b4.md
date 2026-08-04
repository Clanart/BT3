Found a real analog: the amount-conversion path in `pallet-hyper-fungible-token`'s `convert_to_balance` in `on_accept`/`on_timeout`.

### Title
Silent truncation of incoming ERC20 U256 amount when converting to local balance can under/over-credit beneficiaries - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`convert_to_balance` divides the incoming ERC20 `U256` amount by `10^(erc_decimals - local_decimals)`, stringifies the result, and `parse`s it into the runtime's `Balance` type via `FromStr`. Unlike the M-7 report's raw truncating cast, the failure mode here is a silent `FromStr` parse failure or precision loss path that is not bounded against the local `Balance` type's max value, and the divide-then-string-round-trip can mask overflow/underflow in ways a direct numeric cast would not, while `convert_to_erc20` (the inverse, used on the outbound `send` path) has no upper-bound check against `U256::MAX` before being ABI-encoded and dispatched.

### Finding Description
`convert_to_balance` in [1](#0-0)  takes the raw ERC20 `U256` amount decoded from an inbound `Message`, divides by `10^(erc_decimals.saturating_sub(local_decimals))`, converts to a decimal string, and parses it into `Balance` via `FromStr`. This is invoked in `on_accept` at [2](#0-1)  and again in `on_timeout` at [3](#0-2) , with no upper-bound validation of the resulting value against `Balance::MAX` (typically `u128::MAX` on Hyperbridge runtimes) before it is used to `mint_into`/`transfer` funds to the beneficiary. Any EVM-side `HyperFungibleToken`/`WrappedHyperFungibleToken` contract that can send an ISMP `Message` with `amount` close to or above `U256` values whose decimal-scaled quotient exceeds `Balance::MAX` will cause `parse::<B>()` to either fail (`InvalidAmountConversion`, safe failure) or, depending on the concrete `Balance` type's `FromStr` implementation, wrap/truncate in ways not covered by the pallet's tests. The same `Precisions`-driven decimal-scaling assumption is trusted symmetrically in the outbound `convert_to_erc20` at [4](#0-3) , which multiplies a local `u128` by up to `10^(erc_decimals - local_decimals)` with no check that the product stays within `U256`, and no check that the registered `erc_decimals` is sane (an admin-set `Precisions` value is trusted as-is, per `register_token`'s only guard being `config.decimals >= local_decimals` at [5](#0-4) ).

### Impact Explanation
If the decimal-scaling division or string round-trip in `convert_to_balance` ever silently produces a smaller value than the true intended amount for a given inbound message (e.g. due to a `FromStr` implementation on a custom `Balance` type that saturates or wraps rather than erroring, or a precision misconfiguration), a beneficiary receives less than the ERC20 sender actually transferred, while the message is still marked delivered/consumed on the ISMP side — a fund-loss condition analogous to M-7's under-credited `amountStored`. This is a bridge-custody path (native asset escrow/release or non-native mint) governed only by pallet-config-level admin trust, not by proof of the exact numeric bound at the conversion site.

### Likelihood Explanation
Likelihood is constrained by the fact that this path requires a registered, admin-configured token contract and `Precisions` entry — it isn't reachable by an arbitrary unprivileged attacker without first controlling or spoofing a registered EVM contract's message content. Given `on_accept` is only reachable from `IsmpModule::on_accept` after ISMP proof verification and after `ContractToAsset` authentication against `source`/`from`, exploitation is bounded by the same trust assumptions as any registered app; this reduces confidence that a fully unprivileged, proof-honest attacker can reach a value that breaks the conversion under real registered decimal configs (most decimal deltas in practice are small, e.g., 18 vs 10). I could not fully verify the exact `Balance` type's `FromStr` overflow behavior used in production runtimes from the available index, so the severity of the truncation/wrap failure mode (silent loss vs. hard revert) remains unconfirmed.

### Recommendation
Add an explicit checked/saturating bound check in `convert_to_balance` before minting/transferring: reject (rather than silently parse) any decimal-scaled `U256` value that exceeds `Balance::MAX`, and add an explicit overflow check in `convert_to_erc20` guarding the `U256` multiplication against unreasonable `Precisions` deltas. Prefer `TryFrom<U256>` with explicit error propagation over the `to_string()`/`FromStr` round trip so the failure mode is a typed conversion error rather than a possible silent truncation.

### Proof of Concept
Not independently reproducible from the indexed code alone — the existing test in `modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs` (`should_receive_asset_correctly`) only exercises a benign decimals delta (18→10) and does not test boundary/overflow values, so I could not confirm from the index whether a concrete `erc_decimals`/`local_decimals` pair and `U256` amount exist in current runtime configs that trigger silent truncation rather than a clean `InvalidAmountConversion` error. [6](#0-5)

### Citations

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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L84-91)
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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L352-355)
```rust
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
```

**File:** modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs (L51-96)
```rust
#[test]
fn should_receive_asset_correctly() {
	new_test_ext().execute_with(|| {
		// First send to escrow funds in pallet account
		let params = SendParams {
			asset_id: HftNativeAssetId::get(),
			destination: StateMachine::Evm(1),
			recipient: BoundedVec::try_from(BOB.as_slice().to_vec()).unwrap(),
			timeout: 0,
			amount: SEND_AMOUNT,
			relayer_fee: Default::default(),
			call_data: None,
		};

		HyperFungibleToken::send(RuntimeOrigin::signed(ALICE), params).unwrap();
		let balance_after_send = pallet_balances::Pallet::<Test>::free_balance(ALICE);
		assert_eq!(balance_after_send, INITIAL_BALANCE - SEND_AMOUNT);

		// Simulate receiving tokens from EVM
		let module = HyperFungibleToken::default();
		let post = PostRequest {
			source: StateMachine::Evm(1),
			dest: StateMachine::Kusama(100),
			nonce: 0,
			from: hft_contract(),
			to: pallet_hyper_fungible_token::PALLET_ID.to_bytes(),
			timeout_timestamp: 1000,
			body: {
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

		module.on_accept(post).unwrap();
		let new_balance = pallet_balances::Pallet::<Test>::free_balance(ALICE);
		assert_eq!(new_balance, INITIAL_BALANCE);
	});
}
```
