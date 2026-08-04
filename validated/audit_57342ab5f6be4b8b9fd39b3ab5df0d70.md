Based on my investigation of `pallet-relayer`'s fee withdrawal flow, I found a concrete local analog to the H-3 bug-class ("stored/reserved value never reaches the rightful owner").

### Title
Relayer accrued fees are permanently lost if the withdrawal POST request times out - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` zeroes a relayer's accrued `Fees` balance and dispatches a cross-chain POST request that is supposed to instruct the destination chain's `HostManager`/`HYPERBRIDGE_MODULE_ID` to pay out that amount. If that dispatched request never gets delivered and instead times out on the destination, the accrued balance is not restored anywhere in the pallet, so the relayer's legitimately earned reward is permanently lost — exactly analogous to the IchiVaultSpell bug where a value is reserved/calculated for the user but the final transfer back to them is never guaranteed to complete.

### Finding Description
`Pallet::withdraw` in [1](#0-0)  reads `available_amount` from `Fees::<T>::get(...)`, increments the relayer's nonce, builds and dispatches a `DispatchPost` to the destination chain (EVM `HostManager` or the substrate `HYPERBRIDGE_MODULE_ID`), and then unconditionally sets `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero())` immediately after the dispatch call succeeds — i.e., before any confirmation that the request was actually delivered to and processed by the destination.

The dispatch uses `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` (zero fee, zero payer), consistent with the pattern documented for hyperbridge-originated system messages in [2](#0-1) . Because this is a zero-fee message, there is little economic incentive for relayers to prioritize its delivery, and if it times out on the destination (misconfigured `host_manager`, paused destination host, chain issues, etc.), the standard ISMP timeout flow will call the module's `on_timeout` handler for the `from`/`MODULE_ID` that dispatched the request. However, no `IsmpModule` implementation with an `on_timeout` handler was found in `modules/pallets/relayer/` that re-credits `Fees` on timeout — the `withdrawal.rs` module only performs the forward zero-and-dispatch, with no compensating path.

This mirrors the reported bug precisely: `withdrawInternal()` computed `amtLPToRemove`/`amountLpWithdraw` and held tokens back for the user, but never executed the final `doRefund` for LP tokens, permanently stranding them. Here, `withdraw()` "removes" the relayer's accrued balance from `Fees` (the on-chain bookkeeping equivalent of committing the payout) but the actual payout is contingent on cross-chain delivery that is not guaranteed and has no fallback restoration path if it fails.

### Impact Explanation
This falls squarely under "stealing or loss of funds" / bridged assets and relayer rewards must move exactly once and only to the rightful beneficiary. A legitimate relayer that has earned fees through honest delivery work can lose that entire accrued balance permanently, through no fault of their own (e.g., transient destination chain outage, a `HostParams` misconfiguration for that `dest_chain`, or the destination `HostManager`/module simply never processing the request before its ISMP timeout). No malicious relayer, prover, or admin behavior is required — this is a pure protocol-logic gap triggered by ordinary unreliable cross-chain delivery.

### Likelihood Explanation
Any relayer calling the standard, public `withdraw_fees` extrinsic on a chain whose outbound withdrawal message ends up timing out (a realistic and expected occurrence in cross-chain messaging, especially since these system messages are dispatched with zero fee and thus lower relayer priority) will hit this path. It requires no privileged access and no cooperation from any other party — it is purely a function of normal request/timeout dynamics acting on an unprotected balance-zeroing step.

### Recommendation
Do not zero `Fees` until delivery is confirmed, or implement an `on_timeout` handler (mirroring the pattern already used in `HyperFungibleToken.onPostRequestTimeout`, see [3](#0-2)  and `modules/pallets/hyper-fungible-token/src/module.rs` `on_timeout`) that re-credits `Fees::<T>::insert(dest_chain, address, available_amount)` for the relayer if the withdrawal POST request times out, so a failed cross-chain delivery restores the pre-dispatch state rather than silently discarding the funds.

### Proof of Concept
1. A relayer accrues fees for `dest_chain = X` via `accumulate_fees`, building up `Fees::<T>::get(X, relayer) = N`.
2. Relayer calls `withdraw_fees` with `dest_chain = X`. `withdraw()` at [4](#0-3)  dispatches the `DispatchPost` and then sets `Fees::<T>::insert(X, relayer, 0)`.
3. The dispatched request's destination is misconfigured, unreachable, or its `timeout` elapses (e.g., destination `HostManager` for chain `X` becomes unreachable, or `HostParams::<T>::get(X)` config later changes so the message can't be processed on time).
4. The request times out on the destination; the standard ISMP timeout path returns control to hyperbridge's own module registered under `MODULE_ID`/relayer's `from`, but no code in `modules/pallets/relayer/` restores `Fees::<T>` for this commitment.
5. Result: `Fees::<T>::get(X, relayer)` remains `0` permanently, and the relayer never receives the `N` tokens on the destination chain either — total, unrecoverable loss of the accrued reward.

Note: I was not able to locate an `IsmpModule` implementation for `pallet-relayer` handling `on_timeout` within the indexed portion of the repository; if such a handler exists elsewhere and restores `Fees` on timeout, this finding would be invalidated. I recommend explicitly verifying this in a full checkout of the repository, since index size limits may have excluded some file contents.

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

**File:** docs/outbound-request-incentivization.md (L9-11)
```markdown
A regular cross-chain message that flows *through* hyperbridge has a fee attached at origin (the source chain transfers `fee.payer → RELAYER_FEE_ACCOUNT` and records `RequestPayments[commitment]` in pallet-hyperbridge's child trie). When a relayer delivers and the destination receipt lands back on hyperbridge, the existing `accumulate_fees` flow credits that fee to the relayer. That whole pipeline assumes a *user* paid at origin.

But hyperbridge itself originates requests too: host parameter propagation, host-executive updates, intents-coprocessor responses, token-governor messages, the relayer pallet's withdrawal request. Today these all dispatch with `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` (see `modules/pallets/host-executive/src/lib.rs:228`, `modules/pallets/intents-coprocessor/src/lib.rs:486`, `modules/pallets/relayer/src/lib.rs:638`, and `modules/pallets/token-governor/src/impls.rs`). Zero fee, zero payer. So relayers have no economic reason to pick them up, and the only thing that keeps them flowing today is altruism.
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L320-325)
```text
    function onPostRequestTimeout(PostRequestTimeout memory incoming) external override onlyHost whenNotPaused {
        Message memory message = abi.decode(incoming.request.body, (Message));
        address refundee = _toAddr(message.from);
        _mint(refundee, message.amount);
        emit Refunded({to: refundee, amount: message.amount});
    }
```
