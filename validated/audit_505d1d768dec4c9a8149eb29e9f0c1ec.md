## Title
Relayer fee entry zeroed before cross-chain payout succeeds, permanently burning the balance on dispatch failure or destination-side shortfall - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`Pallet::withdraw` in `pallet-relayer` reads a relayer's accrued `Fees` balance, dispatches a fire-and-forget ISMP POST instructing the destination host manager (EVM `IHostManager.withdraw` or the substrate `HYPERBRIDGE_MODULE_ID`'s `WithdrawRelayerFees` handler) to pay it out, and *immediately* zeroes the `Fees` entry on Hyperbridge — before any acknowledgment that the destination chain actually delivered the funds. This mirrors the reported Tap.sol bug-class: an accounting value (`tapped` / here `available_amount`) is consumed/reset without persisting the remainder if the payout on the other side is short, fails, or is capped.

### Finding Description [1](#0-0) 

The flow:
1. `available_amount = Fees::<T>::get(dest_chain, address)` is read.
2. An ISMP `DispatchPost` with `timeout: 0` (never times out) is dispatched to the destination's host manager, carrying `available_amount` as the amount to disburse.
3. Immediately after `dispatch_request` returns `Ok`, the code does `Fees::<T>::insert(dest_chain, address, U256::zero())` — this only proves the *request was queued locally*, not that funds were paid on the destination.

This is the same broken invariant as the Tap.sol report: the withdrawable "allowance" is reset to zero as soon as a withdrawal is initiated, with no bookkeeping to allow a retry or top-up for whatever wasn't actually collected. In Tap.sol, `tapped` is silently truncated to `balance - minimum` and the delta is lost forever once `lastWithdrawals` is updated. Here, `available_amount` is zeroed the moment the local dispatch succeeds, with no confirmation loop back from the destination chain settling the exact amount. If:
- the destination-side host manager contract/pallet reverts the transfer (e.g. insufficient balance in the manager's escrow — the exact "not enough unlocked funds" scenario from the Tap report), or
- the relayer never delivers the request to the destination chain (griefing/liveness failure since delivery is off-chain and not enforced on Hyperbridge), or
- the destination call partially succeeds in a way that under-pays,

there is no path back to hyperbridge to re-credit `Fees`. The `timeout: 0` design explicitly means the request can be delivered "at any time," but nothing forces delivery, and once `Fees` is zero, the relayer's only record of the entitlement is gone. Unlike the `outbound_request` / `outbound_consensus` reward claim pipelines in the same pallet (which use a state-proof-gated `claim_*` extrinsic that pays out only after proving delivery, e.g. `outbound_request.rs` lines 119-197), the relayer's own fee withdrawal path pays out via "dispatch and forget," with the accounting reset happening at dispatch time rather than at proof-of-settlement time.

### Impact Explanation
This falls under "stealing or loss of funds": if the destination host manager's balance is insufficient (a normal operational condition — host manager contracts hold accumulated protocol revenue, which fluctuates) or the withdrawal message is never delivered, the relayer's legitimately accrued fee balance is irrecoverably zeroed with no on-chain proof-gated re-crediting mechanism, unlike the sibling reward-claim flows in the same pallet.

### Likelihood Explanation
This is reachable by any relayer calling the unsigned `withdraw_fees` extrinsic (`modules/pallets/relayer/src/lib.rs` call_index 1) with a valid signature over their own accrued balance — no privileged or malicious-relayer assumption is needed for the *first* trigger (the account is withdrawing its own funds). The only external dependency is whether the destination-side manager can actually pay `available_amount`, which is a state condition, not an attacker action; a relayer can trivially inspect the destination-chain manager's balance/host state before triggering `withdraw_fees` for a value the manager cannot currently pay, or simply withdraw right as a competing large withdrawal drains the manager, hitting the loss deterministically.

### Recommendation
Do not zero `Fees` on local dispatch. Instead:
- Keep the fee entry (or move it to a "pending" bucket) until a destination-chain proof of successful payout is verified back on Hyperbridge (mirroring the `claim_outbound_request_delivery_reward` / `claim_outbound_consensus_delivery_reward` proof-gated settlement pattern already present in this pallet), or
- If keeping the fire-and-forget model, add a reconciliation path: if delivery fails/reverts on destination, emit a receipt hyperbridge can observe and re-credit `Fees` for the unpaid remainder, analogous to the Tap.sol remediation of preserving the undelivered remainder for later withdrawal.

### Proof of Concept
1. Relayer accrues `Fees[dest_chain][relayer] = 1000` via `accumulate_fees`.
2. Destination host manager's fee-token balance drops below 1000 (e.g., after another large withdrawal, or because governance drained it via `IHostManager.withdraw`/`dispatch_withdraw`-style flows).
3. Relayer calls `withdraw_fees` with a valid signature; `Pallet::withdraw` in `modules/pallets/relayer/src/withdrawal.rs` line 116 reads `available_amount = 1000`, dispatches the POST at line 170-175, and unconditionally zeroes `Fees` at line 177.
4. The destination-side `IHostManager.withdraw` call reverts or transfers less than 1000 due to insufficient balance.
5. `Fees[dest_chain][relayer]` is now `0` on Hyperbridge with no record of the unpaid 1000, and no mechanism exists to re-credit it — the funds are permanently lost to the relayer, exactly as the tapped-but-uncollected remainder is lost in the Tap.sol report. [2](#0-1)

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-187)
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

		Self::deposit_event(Event::<T>::Withdraw {
			address: sp_runtime::BoundedVec::truncate_from(address.clone()),
			beneficiary_address: sp_runtime::BoundedVec::truncate_from(beneficiary_address),
			state_machine: withdrawal_data.dest_chain,
			amount: available_amount,
		});

		Ok(())
	}
```
