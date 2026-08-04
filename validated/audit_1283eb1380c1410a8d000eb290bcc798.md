### Title
`Pallet::withdraw` zeroes relayer `Fees` before confirming the destination host can actually pay out the withdrawal - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`modules/pallets/relayer/src/withdrawal.rs::withdraw` reads the relayer's accrued `Fees` balance, dispatches an ISMP POST request instructing the destination `HostManager`/`EvmHost` (or `HYPERBRIDGE_MODULE_ID` on substrate) to pay that amount to a beneficiary, and then unconditionally zeroes the relayer's `Fees` entry on Hyperbridge — before there is any confirmation that the destination chain actually holds enough balance to honor the payout, and before the cross-chain message is even delivered. This is the same optimistic-debt-settlement pattern as the seed report: local state is updated as if payment succeeded, when it is merely dispatched.

### Finding Description
The relevant code: [1](#0-0) 
computes `available_amount` from `Fees::<T>::get(...)`, then: [2](#0-1) 
dispatches `DispatchPost` to the destination (`params.host_manager` on EVM, or `HYPERBRIDGE_MODULE_ID` on Substrate) carrying `WithdrawalParams{ amount: available_amount, ... }`, and — as soon as `dispatch_request` succeeds (meaning only that the outbound ISMP message was queued, not that it was delivered or paid) — immediately zeroes `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero())`.

`dispatch_request` here only enqueues the request into Hyperbridge's outbound commitment trie; it says nothing about whether the destination `EvmHost`/`HostManager::withdraw` will succeed. On the EVM side, `HostManager.onAccept` decodes the `Withdraw` action and calls `IHostManager(_params.host).withdraw(withdrawParams)`, which (per `IHostManager`/`WithdrawParams`, `evm/src/core/EvmHost.sol`) presumably performs an ERC20 transfer of the fee token to the beneficiary. If the `EvmHost` contract's fee-token balance is insufficient at the time the relayer/tesseract task actually delivers and executes this request on the destination, the `SafeERC20` transfer reverts and the `onAccept` call fails — the ISMP request delivery fails or times out.

Because `Fees::<T>::insert(..., U256::zero())` on Hyperbridge already ran at dispatch time (line 177), the relayer's accounted balance is destroyed the moment the withdrawal message is *dispatched*, not when it is confirmed *paid*. If delivery to the destination later fails (insufficient EvmHost balance, host frozen, wrong host-manager address, request times out), the relayer has no recorded balance left to retry with — the funds are optimistically assumed paid and the accounting is not reversed on failure.

This mirrors `_doSherX`'s bug exactly: `calcUnderlying`/`Fees::get` is an optimistic view of "funds owed and payable," and the state is updated (`payOffDebtAll` / `Fees::insert(..., zero)`) assuming the payout will succeed, without any guard for the case where the paying party (the protocol's balance / the destination EvmHost's fee-token balance) cannot actually cover it.

### Impact Explanation
A relayer's legitimately accrued fees can be permanently lost from Hyperbridge's accounting if the destination-side payout fails after dispatch — with no compensating mechanism to restore `Fees`. This is a fund-loss bug for the relayer (the rightful beneficiary never receives the amount, yet Hyperbridge's internal ledger says it was paid), matching the "stealing or loss of funds" and "reward claims / bandwidth accounting must move exactly once and only to the rightful beneficiary and amount" criteria in the bounty scope. There is no privileged actor, malicious relayer, or compromised infrastructure required — the failure mode is simply insufficient destination-side liquidity for the fee token at the moment of delivery, which is a normal operational condition (e.g., EvmHost fee-token treasury temporarily drained by other withdrawals).

### Likelihood Explanation
The window for this to trigger is any point where the destination host's fee-token balance is lower than the sum of in-flight withdrawal requests dispatched against it. Since `Fees` accounting is zeroed unconditionally at dispatch time regardless of eventual delivery outcome, and the only feedback for delivery failure is off-chain retry logic in tesseract (which cannot restore an already-zeroed on-chain `Fees` entry), the invariant "the relayer is credited exactly the amount actually paid" can break under ordinary liquidity conditions, not just adversarial ones.

### Recommendation
Do not zero `Fees::<T>` until the destination-side payout is confirmed (e.g., via a receipt/callback message from the destination chain acknowledging successful transfer), or add a fallback path (e.g., timeout/error handling on the ISMP request) that restores the `Fees` balance for the relayer if the withdrawal request times out or fails to execute on the destination. Alternatively, have `EvmHost`/`HostManager.withdraw` and the substrate withdrawal handler validate destination balance sufficiency before Hyperbridge commits to zeroing the source-side ledger, and propagate failure back to Hyperbridge to reverse the debit.

### Proof of Concept
1. Relayer accrues `Fees[dest_chain][relayer] = X` via `accumulate_fees`.
2. Destination `EvmHost`'s fee-token balance is currently less than `X` (e.g., drained by concurrent withdrawals from other relayers, or protocol fee sweep).
3. Relayer calls `withdraw_fees` → `Pallet::withdraw` (`modules/pallets/relayer/src/withdrawal.rs:81`) with `dest_chain` set to that EVM chain.
4. `dispatch_request` at line 170 succeeds (it only enqueues the message on Hyperbridge); `Fees` is zeroed at line 177 in the same call.
5. The tesseract relayer later delivers the request to the destination `HostManager.onAccept` → `EvmHost.withdraw`, which reverts because the fee-token balance is insufficient (`SafeERC20` transfer failure).
6. The ISMP request delivery fails/times out on the destination side. The relayer's `Fees` entry on Hyperbridge is already `0`; there is no code path in `withdrawal.rs` that restores it. The relayer has permanently lost `X` from their recorded balance despite never receiving payment.

### Citations

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

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-177)
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
```
