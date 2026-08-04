### Title
`pallet-relayer::withdraw` zeroes accrued relayer fees before the cross-chain payout is confirmed, permanently losing funds on destination-side failure - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
This mirrors the `KangarooVault.removeCollateral` bug class: an accounting value is decremented in local storage on the assumption that the corresponding asset movement will succeed, but there is no on-chain link tying the storage write to confirmed completion of that movement. In `pallet-relayer`, `Fees::<T>::insert(..., U256::zero())` runs immediately after dispatching a one-way ISMP `POST` instructing the destination chain (or Hyperbridge itself, for substrate destinations) to pay out `available_amount`. If that dispatched instruction never actually pays out — because the destination-side execution reverts, is mis-configured, or the host-manager cannot cover the transfer — the relayer's already-accrued fee balance is wiped from `Fees` with no code path anywhere in the module to re-credit it.

### Finding Description
In `Pallet::withdraw` [1](#0-0) , the flow is:

1. Read `available_amount = Fees::<T>::get(dest_chain, address)` [2](#0-1) 
2. Build a `DispatchPost` body — either `Message::WithdrawRelayerFees` for substrate destinations, or a `WithdrawalParams` ABI payload targeting the destination's `host_manager` for EVM destinations [3](#0-2) 
3. Dispatch it with `dispatcher.dispatch_request(...)` [4](#0-3) 
4. **Immediately and unconditionally** zero the `Fees` entry: `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());` [5](#0-4) 

The module's own comment confirms the design: *"The on-chain effect is just dispatching the message; the destination chain settles the payout when the ISMP request is delivered there."* [6](#0-5) 

There is no `on_response`/`on_timeout` handling anywhere in `pallet-relayer` that re-credits `Fees` if the dispatched withdrawal instruction fails to execute on the destination. The only related `IsmpModule` implementation, `HyperbridgeWithdrawalModule`, only implements `on_accept` for *inbound* withdrawal instructions from the coprocessor and explicitly rejects `on_response`/`on_timeout` with `CannotHandleMessage` [7](#0-6) . So even if the underlying ISMP layer produced a failure signal for this dispatch, there is no consumer that would restore the zeroed balance.

This is structurally identical to the `KangarooVault.removeCollateral` bug: the storage decrement (`positionData.totalCollateral -= collateralToRemove` / here `Fees::insert(..., 0)`) is performed unconditionally, while the actual value transfer (`EXCHANGE.removeCollateral` / here the cross-chain payout landing on the destination host-manager) is a separate, unguaranteed step that can silently fail, leaving the decremented amount unrecoverable.

### Impact Explanation
If the destination-side settlement of a relayer's withdrawal fails for any reason (e.g. the EVM `host_manager` lacks sufficient balance of `params.fee_token`, the manager contract reverts on a malformed/altered `WithdrawalParams`, the substrate `RELAYER_FEE_ACCOUNT` lacks funds so `T::Currency::transfer` in `HyperbridgeWithdrawalModule::on_accept` errors [8](#0-7) , or governance changes `HostParams` mid-flight), the relayer's entire accrued fee for that `(dest_chain, address)` pair is permanently lost — `Fees` is already zero and nothing ever re-credits it. This is a direct, unrecoverable loss of protocol-owed funds, matching the bounty's "loss of funds" and "logic attack" impact classes.

### Likelihood Explanation
This requires no malicious relayer, prover, or admin action — only an ordinary configuration/funding mismatch on the destination side (e.g. the `RELAYER_FEE_ACCOUNT` or destination `host_manager` running short of the fee token, which is an operationally realistic and even foreseeable condition given multiple relayers can race to drain the same account). Any legitimate relayer calling the public, unsigned-but-verified `withdraw_fees` extrinsic can trigger this loss purely through normal operation; it does not depend on relayer misbehavior, so it is a genuine protocol-level fund-safety gap rather than a "malicious relayer" exclusion.

### Recommendation
Do not zero `Fees` optimistically at dispatch time. Instead:
- Keep the balance debited-but-pending until a destination-side delivery/settlement confirmation is received (e.g., via a receipt/response callback or a subsequent `accumulate`-style proof of successful destination execution), or
- Add an `on_timeout`/failure handler that re-credits `Fees` if the dispatched withdrawal message is not successfully executed on the destination, mirroring how `accumulate_fees` only marks a `RequestCommitments` leaf as `claimed` after verifying delivery via state proof rather than optimistically before dispatch.

### Proof of Concept
1. A relayer accrues fees via `accumulate_fees`, populating `Fees::<T>::get(dest_chain, relayer)` with a nonzero balance.
2. The relayer calls `withdraw_fees` targeting an EVM `dest_chain` whose registered `host_manager`'s `fee_token` balance is insufficient to cover `available_amount` (a state reachable through normal fee-token flow/drainage, not attacker manipulation).
3. `Pallet::withdraw` dispatches the `WithdrawalParams` POST and, in the same call, executes `Fees::<T>::insert(dest_chain, relayer, U256::zero())` unconditionally [9](#0-8) .
4. When the message is delivered to the destination `host_manager`, the transfer fails/reverts due to insufficient balance.
5. `Fees` on Hyperbridge remains permanently zero for that relayer/chain pair — the fee is unrecoverably lost, with no extrinsic or handler in `pallet-relayer` able to restore it.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L29-30)
```rust
//! The on-chain effect is just dispatching the message; the destination chain settles the
//! payout when the ISMP request is delivered there.
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-187)
```rust
	pub fn withdraw(withdrawal_data: WithdrawalInputData) -> DispatchResult {
		let address = match &withdrawal_data.signature {
			Signature::Evm { address, .. } => address.clone(),
			Signature::Sr25519 { public_key, .. } => public_key.clone(),
			Signature::Ed25519 { public_key, .. } => public_key.clone(),
		};

		let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
		let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());

		match &withdrawal_data.signature {
			Signature::Evm { address, .. } => {
				let eth_address = withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
				if &eth_address != address {
					Err(Error::<T>::InvalidPublicKey)?
				}
			},
			Signature::Sr25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
			Signature::Ed25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
		};
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

		Self::deposit_event(Event::<T>::Withdraw {
			address: sp_runtime::BoundedVec::truncate_from(address.clone()),
			beneficiary_address: sp_runtime::BoundedVec::truncate_from(beneficiary_address),
			state_machine: withdrawal_data.dest_chain,
			amount: available_amount,
		});

		Ok(())
	}
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L201-213)
```rust
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

**File:** modules/pallets/ismp/src/dispatcher.rs (L219-226)
```rust
	fn on_response(&self, _response: GetResponse) -> Result<Weight, anyhow::Error> {
		Err(IsmpError::CannotHandleMessage.into())
	}

	fn on_timeout(&self, _request: Request) -> Result<Weight, anyhow::Error> {
		Err(IsmpError::CannotHandleMessage.into())
	}
}
```
