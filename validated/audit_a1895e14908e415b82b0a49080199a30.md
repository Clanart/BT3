## Analog Found: Truncating Fee Accumulator on Substrate Withdrawal Destroys Excess Relayer Funds

### Title
Relayer `Fees` U256 accumulator silently truncated to u128 on substrate withdrawal, permanently burning any balance above `u128::MAX` - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-relayer` stores each relayer's accrued cross-chain delivery fees in a `U256` accumulator (`Fees` storage double-map) that is incremented on every successful `accumulate_fees` proof submission with no upper bound check [1](#0-0) . When a relayer withdraws to a **substrate** destination, the pallet builds the outbound `WithdrawalRequest` by truncating the full `U256` balance down to a `u128` via `available_amount.low_u128()`, then unconditionally zeroes the entire `U256` entry in storage regardless of how much was actually included in the dispatched message [2](#0-1) . This mirrors the TRST-M-5 pattern exactly: an accumulator that keeps growing without a width check, feeding into a narrower field, resulting in silent loss once the value exceeds what the narrower field can represent — except here the mitigation direction is reversed (storage is `U256`, but the disbursement path narrows to `u128` and the bookkeeping is zeroed unconditionally).

### Finding Description
`Fees<T>` is defined as `StorageDoubleMap<_, ..., U256, ValueQuery>` [1](#0-0) . It accumulates via `accumulate_fee_and_deposit_event` / `validate_results`, both of which do unchecked `*inner += fee` on the `U256` entry with no cap [3](#0-2) [4](#0-3) .

When `withdraw()` runs for a substrate `dest_chain`, it reads the full `available_amount: U256` and constructs the wire message with:
```rust
Message::WithdrawRelayerFees(WithdrawalRequest {
    amount: available_amount.low_u128(),
    account: ...,
})
``` [5](#0-4) 

`low_u128()` returns only the low 128 bits of the `U256`, silently discarding any bits above `2^128 - 1`. Regardless of what value was actually dispatched, the function then unconditionally sets the full storage entry to zero:
```rust
Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
``` [6](#0-5) 

and emits `Event::Withdraw { amount: available_amount, ... }` — the *full* pre-truncation value — even though the destination chain (via `HYPERBRIDGE_MODULE_ID`'s `on_accept` handler, which trusts the `amount` field verbatim and calls `T::Currency::transfer`) will only ever disburse the truncated low-128-bit amount [7](#0-6) .

The EVM withdrawal path does not have this problem: it uses `available_amount.into()` into an `alloy_primitives::U256`, which is full-width, so no truncation occurs there — this confirms the bug is specific to the substrate branch [8](#0-7) .

### Impact Explanation
This falls squarely under "stealing or loss of funds" and "transaction manipulation" in the bounty's required-impact list: once a relayer's accrued `Fees[substrate_dest][relayer]` balance exceeds `u128::MAX`, any subsequent `withdraw_fees` call to that substrate destination:
1. Dispatches a request that only pays out `amount mod 2^128`.
2. Zeroes the full `U256` accounting entry as if the entire (much larger) amount had been paid.
3. Permanently and irrecoverably destroys the difference — it is not sent anywhere, not even to a fallback account; it simply vanishes from both the ledger and the wire message.

This is a pure protocol-logic bug reachable through the public, unsigned `withdraw_fees` extrinsic — no malicious relayer, prover, or admin is required; it triggers on ordinary use once the accumulator crosses the u128 boundary.

### Likelihood Explanation
The practical likelihood is constrained by economics: `Fees` is denominated in real fee-token units actually paid by requesters at dispatch time (`RequestPayments`), so reaching `2^128` (~3.4×10^38) in a single relayer/chain bucket requires an extraordinary, unrealistic amount of aggregate fee volume under current fee-token decimal scales. Nonetheless, the code path itself contains no bounds check, no saturating conversion, and no assertion that `available_amount <= u128::MAX` before calling `low_u128()` — the invariant "the accumulator always fits in the disbursement type" is entirely undocumented and unenforced, exactly the same class of oversight identified in the original TRST-M-5 report (an accumulator sized without regard to what consumes it downstream).

### Recommendation
- Before dispatching a substrate withdrawal, assert `available_amount <= U256::from(u128::MAX)` (or check `T::Balance::MAX` generically) and reject/split the withdrawal if it would truncate.
- Alternatively, only zero the portion of `Fees` that was actually included in the dispatched message (`Fees::<T>::mutate(..., |v| *v -= dispatched_amount)`), so any un-dispatched remainder stays claimable instead of being destroyed.
- Add an explicit `checked_into::<u128>()`-style conversion that errors out (`Error::<T>::AmountOverflow`) instead of silently truncating via `low_u128()`.

### Proof of Concept
1. Through repeated legitimate `accumulate_fees` proof submissions (or, in a test harness, by directly inserting into storage as the pallet's own tests do: `Fees::<T>::insert(chain, addr, U256::from(u128::MAX) + U256::from(1000))` [9](#0-8) ), push a relayer's `Fees[SubstrateChain][relayer]` entry to `u128::MAX + 1000`.
2. Call `withdraw_fees` with `dest_chain` set to that substrate chain.
3. `available_amount.low_u128()` returns `999` (wraps), so the dispatched `WithdrawalRequest.amount` is `999` instead of `u128::MAX + 1000`.
4. `Fees::<T>::insert(dest_chain, address, U256::zero())` unconditionally clears the full balance.
5. The destination chain pays out only `999` fee-token units to the relayer; the remaining `u128::MAX` worth of accrued fees is permanently lost with no path to recovery, while the emitted `Event::Withdraw` still reports the full pre-truncation `available_amount`, masking the loss from off-chain observers.

### Citations

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

**File:** modules/pallets/relayer/src/accumulate.rs (L287-298)
```rust
			let encoded_receipt = dest_result
				.get(&dest_key)
				.cloned()
				.flatten()
				.ok_or_else(|| Error::<T>::ProofValidationError)?;
			let address = Self::decode_receipt_relayer(
				proof.dest_proof.height.id.state_id,
				&encoded_receipt,
			)?;
			let entry = result.entry(address).or_insert(U256::zero());
			*entry += fee;
			commitments.push(commitment);
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-361)
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
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L197-213)
```rust
		let message = Message::<T::AccountId, T::Balance>::decode(&mut &request.body[..])
			.map_err(|err| IsmpError::Custom(format!("Failed to decode message: {err:?}")))?;

		match message {
			Message::WithdrawRelayerFees(WithdrawalRequest { account, amount }) => {
				T::Currency::transfer(
					&RELAYER_FEE_ACCOUNT.into_account_truncating(),
					&account,
					amount,
					Preservation::Expendable,
				)
				.map_err(|err| {
					IsmpError::Custom(format!("Error withdrawing protocol fees: {err:?}"))
				})?;

				Pallet::<T>::deposit_event(Event::<T>::RelayerFeeWithdrawn { amount, account });
			},
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp_relayer.rs (L930-963)
```rust
#[test]
fn test_withdrawal_fees() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		let pair = sp_core::sr25519::Pair::from_seed_slice(H256::random().as_bytes()).unwrap();
		let public_key = pair.public().0.to_vec();
		pallet_ismp_relayer::Fees::<Test>::insert(
			StateMachine::Kusama(2000),
			public_key.clone(),
			U256::from(250_000_000_000_000_000_000u128),
		);
		let message = message(0, StateMachine::Kusama(2000), None);
		let signature = pair.sign(&message).0.to_vec();

		let withdrawal_input = WithdrawalInputData {
			signature: Signature::Sr25519 { public_key: public_key.clone(), signature },
			beneficiary: None,
			dest_chain: StateMachine::Kusama(2000),
		};

		pallet_ismp_relayer::Pallet::<Test>::withdraw_fees(
			RuntimeOrigin::none(),
			withdrawal_input.clone(),
		)
		.unwrap();
		assert_eq!(
			pallet_ismp_relayer::Fees::<Test>::get(StateMachine::Kusama(2000), public_key.clone()),
			U256::zero()
		);

		assert_eq!(
			pallet_ismp_relayer::Nonce::<Test>::get(public_key, StateMachine::Kusama(2000)),
			1
		);
```
