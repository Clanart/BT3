Found it: `withdrawal.rs` line 138 uses `available_amount.low_u128()` on a `U256` fee balance when dispatching a substrate withdrawal, which is exactly the "unsafe cast truncation" pattern from the XVSVault report.

### Title
Silent truncation of accumulated relayer fees via `U256::low_u128()` in substrate fee withdrawal - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` reads the relayer's accrued fee balance as a `U256` from the `Fees` storage map and, for substrate destinations, truncates it to `u128` with `available_amount.low_u128()` before building the `WithdrawalRequest` that is dispatched cross-chain. `low_u128()` silently discards the high 128 bits instead of erroring or saturating — the same unsafe-narrowing pattern flagged in the XVSVault report (`uint96()` cast instead of `safe96()`).

### Finding Description
`Fees<T>` stores relayer earnings as `U256` [1](#0-0) , and fees are accumulated without any bound checks by `+=` in `accumulate.rs` [2](#0-1)  and in `accumulate_fee_and_deposit_event` [3](#0-2) , so nothing on the accrual path prevents `available_amount` from exceeding `u128::MAX`.

When a relayer calls `withdraw()`, the full un-truncated `available_amount` is used for the `min_withdrawal_amount` check, for the zeroing of the `Fees` entry, and for the emitted event amount [4](#0-3) [5](#0-4) . But the actual payout instruction dispatched to the destination chain for substrate destinations uses `available_amount.low_u128()`: [6](#0-5) 

`low_u128()` (from `primitive_types::U256`) returns only the low 128 bits of the 256-bit value with no overflow check — this is the direct analog of `uint96()` unsafe casts in the reported bug. If `available_amount` ever exceeds `u128::MAX`, the `WithdrawalRequest.amount` field silently wraps to a much smaller number while:
- `Fees::<T>::insert(..., U256::zero())` zeroes out the full, un-truncated balance (line 177), and
- the `Event::Withdraw` emits the full, un-truncated `available_amount` (line 183),

creating an accounting mismatch: the pallet believes the full amount was disbursed and zeroes the ledger accordingly, but the dispatched cross-chain instruction only pays out the truncated remainder. Unlike the EVM branch (`amount: available_amount.into()` at line 152), which uses a lossless `U256`-to-`U256`-ABI conversion, only the substrate branch performs this narrowing cast.

### Impact Explanation
If a relayer's accrued `Fees` balance is driven above `u128::MAX` (a realistic threshold only in absolute integer terms — bridge fee tokens frequently use 18 decimals, so `u128::MAX` is still an enormous but not impossible sum to accumulate over enough deliveries/time, and nothing in `accumulate()` caps per-call or cumulative fee growth), the relayer's on-chain ledger (`Fees` map) is zeroed as if the correct amount were paid, while the destination chain only receives the wrapped, truncated amount. This is a direct fund-loss/mis-accounting bug in the reward-claim settlement path: the protocol loses track of the discrepancy between what it recorded as paid and what was actually transferred, and the difference is neither recoverable by the relayer (ledger already zeroed) nor retained by the treasury in a traceable way.

### Likelihood Explanation
Reaching `u128::MAX` in a single fee balance is an extreme, likely impractical amount under realistic fee-token economics, so this is a low-likelihood/high-severity latent bug rather than an easily exploitable one today. It requires the accumulated `U256` fee balance to genuinely exceed `u128::MAX`, which the current accrual/dispatch pipeline never checks for or prevents.

### Recommendation
Replace `available_amount.low_u128()` with a checked conversion (`u128::try_from(available_amount)` or `TryInto`) and reject the withdrawal (or split it into `u128`-sized chunks) if the amount does not fit, mirroring the safe-cast recommendation from the reference report. Alternatively, bound `Fees<T>` growth so it can never exceed `u128::MAX`, and make the EVM/substrate amount-conversion paths symmetric so a future refactor can't silently reintroduce the narrowing cast on the EVM side either.

### Proof of Concept
Conceptual reproduction (requires an accrual mechanism able to push a single relayer's `Fees` entry above `u128::MAX`, which is not currently rate-limited in `accumulate()`):
1. Accumulate relayer fees for a given `(dest_chain, address)` via repeated `accumulate_fees` proofs until `Fees::<T>::get(dest_chain, address) > u128::MAX`.
2. Call `withdraw_fees` for a substrate `dest_chain`.
3. Observe that `WithdrawalRequest.amount = available_amount.low_u128()` wraps to `available_amount % 2^128`, while `Fees::<T>::insert(dest_chain, address, U256::zero())` zeroes the full balance and `Event::Withdraw.amount` reports the full pre-truncation value — i.e., the ledger and event show a payout larger than what the dispatched cross-chain message actually instructs the destination to pay.

### Citations

**File:** modules/pallets/relayer/src/lib.rs (L111-121)
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
```

**File:** modules/pallets/relayer/src/accumulate.rs (L134-144)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});
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

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-123)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}
```

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
