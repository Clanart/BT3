Found it. Line 138 of `modules/pallets/relayer/src/withdrawal.rs` is the direct analog: `available_amount.low_u128()` silently truncates a `U256` fee balance down to `u128` when the withdrawal destination is a substrate chain.

### Title
Silent `U256`→`u128` truncation of relayer fee balance via `low_u128()` on substrate withdrawal path - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` reads the relayer's accumulated fee balance as `U256` from `Fees::<T>::get(...)` and, for substrate destinations, truncates it with `.low_u128()` when building the `WithdrawalRequest` dispatched to `HYPERBRIDGE_MODULE_ID` [1](#0-0) . This mirrors the M-7 pattern exactly: a `uint256`-denominated balance is unsafely narrowed to a smaller integer type before being used as the transferred amount, while the storage that is zeroed and the event that is emitted still use the full, untruncated `available_amount` [2](#0-1) .

### Finding Description
`Fees` is a `StorageDoubleMap` keyed by `(StateMachine, Vec<u8>)` storing a `U256` per relayer [3](#0-2) , and fees accumulate unboundedly via `accumulate_fee_and_deposit_event`, which does `*inner += fee` with no upper-bound check [4](#0-3) . When a relayer calls `withdraw`, for a substrate destination the dispatched `WithdrawalRequest.amount` is computed as `available_amount.low_u128()` [1](#0-0) . `U256::low_u128()` returns only the low 128 bits and does not error or saturate on overflow — if `available_amount >= 2^128`, the dispatched amount silently wraps to `available_amount mod 2^128`, which can be far smaller than the real balance (or even zero, exactly as in the M-7 report where `type(uint128).max + 1` wraps to `0`).

Despite the dispatched (and therefore actually paid) amount being wrong, the pallet:
- Zeroes the full `Fees` entry regardless of the truncated payout: `Fees::<T>::insert(..., U256::zero())` [5](#0-4) .
- Emits the `Withdraw` event with the untruncated `available_amount`, masking the discrepancy from downstream consumers/indexers [6](#0-5) .

No guard exists anywhere in `withdraw` checking `available_amount` against `u128::MAX` before taking the substrate branch.

### Impact Explanation
This is a loss-of-funds bug for the relayer, matching the "stealing or loss of funds" impact category. A relayer whose accumulated cross-chain fee balance exceeds `u128::MAX` (2^128 fee-token base units — plausible for high-decimal fee tokens accumulated over a long, uninterrupted period, or via repeated fee accrual without withdrawal) will have their `Fees` balance zeroed on-chain while the actual amount dispatched to the destination chain for payout is the wrapped, truncated value — potentially far less than owed, including exactly `0` at the boundary. This is a one-way, irreversible fund loss: once `Fees::<T>::insert(..., U256::zero())` executes, the original balance is unrecoverable.

### Likelihood Explanation
Reaching the vulnerable branch requires no privileged role — any relayer that has accumulated fees can call `withdraw` for a substrate `dest_chain` (`s if s.is_substrate()`), which is a normal, permissionless relayer action available in production. The only precondition is that `available_amount` (a `U256`, unbounded by any cap in `accumulate_fee_and_deposit_event`) exceeds `u128::MAX`. This is a high, but not universal, bar under current fee-token economics, so likelihood is moderate — it depends on accumulated volume, but the code path itself has no defense whatsoever if that threshold is crossed.

### Recommendation
Replace the truncating `.low_u128()` conversion with a checked conversion (e.g. `u128::try_from(available_amount)` or `TryInto`) and reject/short-circuit the withdrawal (returning an error such as `AmountOverflow`) instead of silently wrapping when `available_amount > u128::MAX`. Alternatively, change `WithdrawalRequest::amount` to `U256` throughout the substrate withdrawal path so no narrowing is required, consistent with how the EVM branch already forwards `available_amount.into()` without truncation to a `U256`-based ABI param [7](#0-6) .

### Proof of Concept
1. A relayer accumulates fees on a substrate `dest_chain` across enough deliveries (or from a fee-token/state-machine combination with very high per-message fees) such that `Fees::<T>::get(dest_chain, relayer) > u128::MAX` in `accumulate_fee_and_deposit_event` [8](#0-7) .
2. The relayer signs and submits `withdraw` for that `dest_chain`.
3. Inside `withdraw`, `available_amount.low_u128()` truncates the balance, e.g. `available_amount = 2^128` truncates to `0` [9](#0-8) .
4. The dispatched `WithdrawalRequest` carries `amount = 0` (or whatever the wrapped remainder is) to `HYPERBRIDGE_MODULE_ID`, so the destination pays out little or nothing.
5. `Fees::<T>::insert(dest_chain, relayer, U256::zero())` unconditionally zeroes the real balance [5](#0-4) , permanently erasing the difference between the true balance and the truncated payout.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L134-143)
```rust
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
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L149-155)
```rust
				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L177-184)
```rust
		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());

		Self::deposit_event(Event::<T>::Withdraw {
			address: sp_runtime::BoundedVec::truncate_from(address.clone()),
			beneficiary_address: sp_runtime::BoundedVec::truncate_from(beneficiary_address),
			state_machine: withdrawal_data.dest_chain,
			amount: available_amount,
		});
```

**File:** modules/pallets/relayer/src/lib.rs (L111-122)
```rust
	/// double map of address to source chain, which holds the amount of the relayer address
	#[pallet::storage]
	#[pallet::getter(fn relayer_fees)]
	pub type Fees<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		StateMachine,
		Blake2_128Concat,
		Vec<u8>,
		U256,
		ValueQuery,
	>;
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-368)
```rust
	pub fn accumulate_fee_and_deposit_event(
		state_machine: StateMachine,
		address: Vec<u8>,
		fee: U256,
	) {
		let _ = Fees::<T>::try_mutate(state_machine, address.clone(), |inner| {
			*inner += fee;
			Ok::<(), ()>(())
		});

		Self::deposit_event(Event::<T>::AccumulateFees {
			address: sp_runtime::BoundedVec::truncate_from(address),
			state_machine,
			amount: fee,
		});
	}
```
