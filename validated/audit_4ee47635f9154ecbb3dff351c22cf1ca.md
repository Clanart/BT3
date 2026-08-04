## Analysis

The SonicStaking bug's core pattern is: **an internal accounting update is finalized in the same transaction as an external fund-move call, but if that external call's failure path is unhandled/unaccounted for, the internal ledger gets out of sync with the real, executed transfer** — either losing track of a loss, or (as here) zeroing a claim before the actual payout is confirmed to succeed.

A structurally identical pattern exists in Hyperbridge's relayer fee withdrawal flow.

### Where it lives

`Pallet::withdraw` in `modules/pallets/relayer/src/withdrawal.rs` increments the relayer's nonce, dispatches an ISMP `WithdrawRelayerFees` POST request, and then unconditionally zeroes the relayer's `Fees` entry — all based only on whether the *dispatch* (i.e., queuing the message) succeeded, not on whether the downstream transfer that message triggers ever succeeds: [1](#0-0) 

The actual fund movement happens later, asynchronously, when that POST request is delivered and processed by `HyperbridgeWithdrawalModule::on_accept`: [2](#0-1) 

That handler calls `T::Currency::transfer(&RELAYER_FEE_ACCOUNT..., &account, amount, Preservation::Expendable)`. If this transfer fails — e.g., `RELAYER_FEE_ACCOUNT` lacks sufficient free balance to cover `amount` (existential-deposit edge cases, concurrent withdrawals racing against the same pooled account, or any other `Currency::transfer` error) — `on_accept` returns `Err`, and the ISMP message delivery for that specific request fails on the destination side.

Meanwhile, on the source side, `Fees::<T>::insert(..., U256::zero())` already ran unconditionally at dispatch time — the relayer's claim was already zeroed before any confirmation that the actual payout would succeed. Compounding this, the dispatched `DispatchPost` uses `timeout: 0`: [3](#0-2) 

A zero timeout means there is no timeout-triggered refund path to restore the zeroed `Fees` balance if delivery/execution ultimately fails. The relayer's nonce is also already incremented (line 127-131), so even a retried signed withdrawal for the same amount cannot be resubmitted with the same signature.

### Title
Relayer fee accounting is zeroed optimistically before downstream withdrawal execution can fail, permanently stranding accrued relayer fees - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` zeroes a relayer's `Fees` balance as soon as the ISMP dispatch call succeeds, without waiting for the corresponding `HyperbridgeWithdrawalModule::on_accept` currency transfer (in `modules/pallets/ismp/src/dispatcher.rs`) to actually complete. If that downstream transfer fails, the relayer's claim is already erased, the nonce is already advanced, and the zero-timeout request has no automatic refund path — mirroring the SonicStaking `operatorExecuteClawBack` pattern where an unhandled downstream failure leaves the internal ledger permanently unreconciled with reality.

### Finding Description
`withdraw()` performs, in order: (1) verify signature, (2) increment `Nonce`, (3) `dispatcher.dispatch_request(...)` (only checked for the local queuing step), (4) unconditionally `Fees::<T>::insert(..., U256::zero())`. Step 4 does not depend on whether the request is ever successfully delivered and executed by `HyperbridgeWithdrawalModule::on_accept`, whose `T::Currency::transfer` from `RELAYER_FEE_ACCOUNT` can fail for ordinary `Currency` reasons (insufficient balance, existential-deposit/keep-alive violations under `Preservation::Expendable`, etc.), returning an `Err` that fails message execution at the destination. Because `timeout: 0` is used, there is no timeout window in which failed delivery triggers an automatic on-chain refund of the zeroed `Fees` entry, and the relayer's nonce has already moved past the value used to authorize this withdrawal, preventing a clean resubmission of the same claim.

### Impact Explanation
This causes a genuine loss of funds for the relayer: the accrued/earned relayer fee accounting (`Fees::<T>`) is destroyed before the payout is guaranteed, and there is no built-in recovery mechanism (no timeout refund, no nonce-safe retry) once the downstream transfer fails. This falls under "stealing or loss of funds" and "logic attacks" in the impact gate, since it is a reachable, unprivileged-relayer-triggered path (any relayer calling the permissionless `withdraw_fees` extrinsic) that leads to unrecoverable loss of legitimately earned funds.

### Likelihood Explanation
The trigger condition is any failure of the `RELAYER_FEE_ACCOUNT` transfer at delivery time — e.g., concurrent withdrawals racing the shared `RELAYER_FEE_ACCOUNT` pool, or the account balance being tighter than the sum of pending `Fees` claims. This does not require a malicious peer, relayer, or admin — it can occur under normal operational conditions whenever the pooled relayer-fee account's actual on-chain balance and the sum of outstanding `Fees::<T>` claims diverge, which the code does nothing to prevent since claims are honored purely from a separate ledger rather than an escrow reserved at accrual time.

### Recommendation
Do not zero `Fees::<T>` optimistically at dispatch time. Either: (a) zero the fee entry only after receiving confirmation (via a response/ack path) that `HyperbridgeWithdrawalModule::on_accept` succeeded, or (b) set a non-zero `timeout` on the `DispatchPost` and implement an `on_timeout` handler that restores the `Fees` balance (and/or reverts the nonce) if delivery/execution ultimately fails, mirroring how `onPostRequestTimeout` is expected to refund payers elsewhere in the protocol.

### Proof of Concept
1. Two relayers (or the same relayer via two withdrawal calls in quick succession across different `dest_chain`s) each have `Fees::<T>` entries whose sum exceeds the actual free balance currently held by `RELAYER_FEE_ACCOUNT`.
2. Relayer A calls `withdraw_fees`; `Nonce` increments, `dispatch_request` succeeds (it only queues the message), `Fees::<T>` is zeroed for A.
3. Relayer B calls `withdraw_fees` similarly for a larger amount; same sequence, `Fees::<T>` zeroed for B.
4. When B's `WithdrawRelayerFees` message is processed by `HyperbridgeWithdrawalModule::on_accept`, `RELAYER_FEE_ACCOUNT` no longer has enough balance (A's transfer already drained it), so `T::Currency::transfer` fails and `on_accept` returns `Err`.
5. B's request fails to execute; because `timeout: 0`, there is no timeout refund. B's `Fees` entry was already zeroed and their `Nonce` already advanced, so B has permanently lost their earned fee with no on-chain remediation.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-184)
```rust
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
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L189-217)
```rust
impl<T: Config> IsmpModule for HyperbridgeWithdrawalModule<T> {
	fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
		// Only the configured coprocessor may instruct withdrawals.
		let source = request.source;
		if Some(source) != T::Coprocessor::get() {
			Err(IsmpError::Custom(format!("Invalid request source: {source}")))?
		}

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
		}

		Ok(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 0))
	}
```
