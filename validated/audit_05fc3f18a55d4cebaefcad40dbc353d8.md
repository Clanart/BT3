Found it. Line 138 of `withdrawal.rs`: for substrate destinations, the accumulated relayer fee (a `U256` accumulated in `accumulate_fees` from cross-chain proof verification, capable of holding arbitrarily large proven fee totals) is narrowed with `available_amount.low_u128()` when building the `WithdrawRelayerFees` message, while the EVM path (`available_amount.into()`) preserves the full `U256`.

### Title
Truncating `U256` relayer fee to `u128` via `low_u128()` on substrate withdrawal path silently drops high-order bits, causing under/mis-payment - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`Pallet::withdraw` computes `available_amount: U256` from `Fees::<T>::get(...)`, which is accumulated over many `accumulate_fees` calls (`modules/pallets/relayer/src/accumulate.rs`) as proven relayer-fee amounts from source-chain state proofs, each up to `U256::MAX`. When the destination chain is a substrate chain, the withdrawal amount sent in the dispatched `WithdrawRelayerFees` message is computed via `available_amount.low_u128()` [1](#0-0) , which silently discards any bits above the low 128, whereas the EVM branch uses the full value via `available_amount.into()` [2](#0-1) .

### Finding Description
`Fees` is stored as `U256` [3](#0-2) , and is accumulated from proven per-request fee amounts across many requests via `accumulate_fees`. There is no cap enforcing that the accumulated total stays within `u128` range — it is only bounded by `U256::MAX`, and normal, legitimate accumulation over enough requests/time (or a source chain using large fee-token amounts, e.g. 18-decimal fee tokens with high per-request fees) can exceed `u128::MAX` (~3.4e38).

When `withdraw` is called for a **substrate** `dest_chain`, the code path at line 138 truncates via `.low_u128()` instead of failing or capping safely:
```rust
Message::WithdrawRelayerFees(WithdrawalRequest {
    amount: available_amount.low_u128(),
    ...
```
Meanwhile, the state that gets zeroed and the event that gets emitted still use the full, un-truncated `available_amount`:
```rust
Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
Self::deposit_event(Event::<T>::Withdraw { ... amount: available_amount, ... });
```
So `Fees` is zeroed for the full amount, the `Withdraw` event reports the full amount, but the actual instruction dispatched to the destination chain (which is what triggers the real token movement there) carries only the low 128 bits — a value that can be far smaller than what was actually owed and cleared from the ledger.

### Impact Explanation
This is a fund-loss bug matching the bounty's "loss of funds" / "transaction manipulation" categories: a relayer's rightfully accumulated and proven fee balance is irrecoverably reduced once withdrawn to a substrate destination, because `Fees` is zeroed for the full `U256` amount but the dispatched payout instruction only reflects `amount mod 2^128`. The difference (`available_amount - low_u128(available_amount)`) is permanently lost — it is neither paid out nor retained in the `Fees` ledger for a future withdrawal, since the entry was already zeroed at line 177.

### Likelihood Explanation
This requires no attacker action, malicious relayer, or unauthorized access — it is triggered by an honest relayer withdrawing a legitimately large accumulated fee balance to a substrate destination chain. The likelihood scales with fee-token decimals and the number/size of relayer fees accumulated (this pallet's own `Fees` storage is `U256`, explicitly built to hold large fee accumulations, so hitting >2^128 is well within the intended value space rather than a corner case).

### Recommendation
Use the full `U256` value (or a checked conversion that errors rather than truncates) on the substrate path as well, mirroring the EVM branch's `available_amount.into()`. If the destination's `WithdrawalRequest::amount` type is fundamentally `u128`, either bound `Fees` accumulation to `u128` with a checked/saturating add and reject/split further accumulation once near the cap, or reject `withdraw()` with an explicit error when `available_amount > u128::MAX` instead of silently truncating.

### Proof of Concept
1. Over time (or via one/few large proven relayer fees on a high-decimal fee token), a relayer's `Fees::<T>::get(dest_chain, relayer)` accumulates to a `U256` value greater than `u128::MAX`, e.g. `U256::MAX / 2`.
2. The relayer calls `withdraw()` targeting a substrate `dest_chain`.
3. `available_amount.low_u128()` truncates the value to its low 128 bits — a value potentially many orders of magnitude smaller than the real balance.
4. `Fees` is zeroed for the *full* `U256` amount and the `Withdraw` event reports the full amount, but the dispatched `WithdrawRelayerFees` message instructs the destination to pay only the truncated (much smaller) amount.
5. The relayer permanently loses the difference — no further claim is possible since the ledger entry is already zero. [4](#0-3)

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-177)
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

		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};

		// Account is not useful in this case
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
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
