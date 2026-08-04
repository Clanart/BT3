## Finding

### Title
`MinWithdrawal` default assumes 18-decimal fee tokens, permanently blocking relayer fee withdrawals on lower-decimal chains - ([File: modules/pallets/relayer/src/lib.rs], [File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
The `pallet-ismp-relayer` accumulates relayer fees per `(dest_chain, address)` in raw units of that destination chain's configured `fee_token`, but gates withdrawal against a hardcoded `MinWithdrawal` constant that assumes every fee token has 18 decimals. For any destination chain whose fee token has fewer decimals (e.g. 6-decimal USDC, confirmed elsewhere in this codebase as the "Base-style"/"BSC-style" fee token), the default minimum-withdrawal threshold becomes many orders of magnitude too large, effectively locking relayer-earned fees until governance manually intervenes per-chain.

### Finding Description
`Fees<T>` stores the amount a relayer can withdraw from a given destination chain, accumulated directly from the raw `fee` value read from source-chain request metadata: [1](#0-0) 

This value is denominated in the destination chain's native fee-token units — confirmed by `withdraw()`, which passes the raw `available_amount` straight through to `WithdrawalParams.amount` together with `params.fee_token` (the destination's own configured fee token), with no decimal normalization: [2](#0-1) 

The withdrawal gate compares this raw, chain-native-decimal amount against a hardcoded 18-decimal constant: [3](#0-2) 

and the check itself: [4](#0-3) 

`MinimumWithdrawalAmount` is an `OptionQuery` map that only falls back to `MinWithdrawal::get()` (`10 * 10^18`) when governance has not explicitly called `set_minimum_withdrawal` for that specific `StateMachine`: [5](#0-4) 

If the destination chain's fee token has, say, 6 decimals, "$10" in that token's raw units is `10 * 10^6`, but the default threshold enforced on-chain is `10 * 10^18` — twelve orders of magnitude too high. Elsewhere in the repo, fee tokens with 6 decimals are explicitly treated as a normal, expected configuration (e.g. "Base-style" USDC), confirming this isn't a theoretical decimal value: [6](#0-5) 

This is the exact bug class from the external report — a value expressed in fixed 18-decimal units compared against amounts whose real decimals vary per asset/chain — reproduced here for the on-chain relayer withdrawal gate, whereas the off-chain tesseract relayer code correctly scales its own pre-submission check by the destination's real `fee_token_decimals()`: [7](#0-6) 

That off-chain scaling does not fix the on-chain enforcement: the extrinsic `withdraw_fees` independently re-checks against `Self::min_withdrawal_amount(...).unwrap_or(MinWithdrawal::get())` inside the pallet itself, so even a correctly-scaled off-chain submission will still fail on-chain if governance never configured a per-chain override.

### Impact Explanation
For any destination chain added to the protocol whose fee token has fewer than 18 decimals and for which governance has not yet (or ever) called `set_minimum_withdrawal`, relayers' legitimately accrued fees on that chain become unwithdrawable through the standard extrinsic path — the required balance to clear the default threshold is astronomically larger than any realistic accumulated fee. This is a fund-lock condition: value legitimately owed to relayers is inaccessible, not through any protocol design intent but through a decimals mismatch in a hardcoded default. It requires no malicious actor, prover, relayer, or admin — only the absence of a per-chain governance configuration, which is the same "someone forgot to convert decimals" root cause as the seed report.

### Likelihood Explanation
This triggers automatically and silently whenever the protocol is extended to a new EVM chain whose configured `fee_token` has non-18 decimals (a supported, documented configuration in this codebase) and before governance explicitly sets `MinimumWithdrawalAmount` for that specific `StateMachine`. No attacker action is needed; the condition is the default state of the system for any newly onboarded low-decimal-fee-token chain.

### Recommendation
Scale `MinWithdrawal`'s default (and any governance-set `MinimumWithdrawalAmount`) by the actual fee-token decimals of the destination chain rather than assuming 18 decimals uniformly — e.g., fetch `HostParams::<T>::get(state_machine)` and derive `fee_token` decimals to compute the default threshold, or require `set_minimum_withdrawal` to be mandatorily configured before a chain is activated for relayer withdrawals, failing closed rather than falling back to an 18-decimal-denominated constant.

### Proof of Concept
1. Governance onboards a new destination `StateMachine` (e.g. an EVM chain) whose `HostParams::fee_token` is a 6-decimal stablecoin, without calling `set_minimum_withdrawal` for that chain.
2. A relayer delivers messages to/from this chain and calls `accumulate_fees`, which credits `Fees::<T>::get(state_machine, relayer_address)` with raw 6-decimal fee-token units (e.g. accumulating hundreds of dollars worth of fees, `total_fee` ≈ `10^8`–`10^9` raw units).
3. The relayer calls `withdraw_fees` (or the off-chain `tesseract` `withdraw`/`auto_withdraw` path, which correctly computes its own threshold but still submits to the same extrinsic).
4. `withdraw()` compares `available_amount` (~`10^9`) against `MinimumWithdrawalAmount::<T>::get(state_machine).unwrap_or(MinWithdrawal::get())` = `10 * 10^18`, at `modules/pallets/relayer/src/withdrawal.rs:116-123`.
5. `available_amount < min_amount` is always true regardless of how much the relayer has legitimately earned (short of accumulating ~`10^13` USDC), so `Error::<T>::NotEnoughBalance` is returned every time, and the relayer's fees remain permanently locked in `Fees<T>` until governance manually calls `set_minimum_withdrawal` for that chain.

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L260-286)
```rust
			let fee = match proof.source_proof.height.id.state_id {
				s if crate::is_pharos(&s) =>
					if encoded_metadata.len() == 32 {
						U256::from_big_endian(&encoded_metadata)
					} else {
						return Err(Error::<T>::ProofValidationError);
					},
				s if s.is_evm() => {
					use alloy_rlp::Decodable;
					let fee = alloy_primitives::U256::decode(&mut &*encoded_metadata)
						.map_err(|_| Error::<T>::ProofValidationError)?;
					U256::from_big_endian(&fee.to_be_bytes::<32>())
				},
				s if s.is_substrate() => {
					use codec::Decode;
					let fee: u128 = pallet_ismp::dispatcher::RequestMetadata::<T>::decode(
						&mut &*encoded_metadata,
					)
					.map_err(|_| Error::<T>::ProofValidationError)?
					.fee
					.fee
					.into();
					U256::from(fee)
				},
				// unsupported
				_ => Err(Error::<T>::MismatchedStateMachine)?,
			};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-159)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;

		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
			_ => {
				let HostParam::EvmHostParam(params) =
					HostParams::<T>::get(withdrawal_data.dest_chain)
						.ok_or_else(|| Error::<T>::MissingMangerAddress)?;

				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;

				(params.host_manager.0.to_vec(), body)
			},
		};
```

**File:** modules/pallets/relayer/src/lib.rs (L137-150)
```rust
	/// Default minimum withdrawal is $10
	pub struct MinWithdrawal;

	impl Get<U256> for MinWithdrawal {
		fn get() -> U256 {
			U256::from(10u128 * 1_000_000_000_000_000_000)
		}
	}

	/// Minimum withdrawal amount
	#[pallet::storage]
	#[pallet::getter(fn min_withdrawal_amount)]
	pub type MinimumWithdrawalAmount<T: Config> =
		StorageMap<_, Blake2_128Concat, StateMachine, U256, OptionQuery>;
```

**File:** modules/pallets/relayer/src/lib.rs (L370-381)
```rust
		/// Sets the minimum withdrawal amount using the correct decimals
		#[pallet::call_index(2)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 1))]
		pub fn set_minimum_withdrawal(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			amount: u128,
		) -> DispatchResult {
			T::RelayerOrigin::ensure_origin(origin)?;
			MinimumWithdrawalAmount::<T>::insert(state_machine, U256::from(amount));
			Ok(())
		}
```

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L149-152)
```text
    MockOracle usdcOracle;
    MockToken usdc6; // 6-decimal USDC (Base-style)
    MockToken usdc18; // 18-decimal USDC (BSC-style)
    MockV2Router router;
```

**File:** tesseract/messaging/messaging/src/fees.rs (L122-137)
```rust
					let amount = hyperbridge.available_amount(client.clone(), &chain).await?;
					let fee_token_decimals = client.fee_token_decimals().await?;
					let min_amount: U256 = (config
						.minimum_withdrawal_amount
						.map(|val| std::cmp::max(val, 10))
						.unwrap_or(100) as u128 *
						10u128.pow(fee_token_decimals.into()))
					.into();
					if amount < min_amount {
						tracing::info!(
							target: crate::LOG_TARGET, unclaimed = %amount,
							min = %min_amount,
							"balance below threshold; skipping",
						);
						return Ok::<_, anyhow::Error>(());
					}
```
