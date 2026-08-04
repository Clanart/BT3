### Title
Relayer fee withdrawal dispatches a fixed `amount` with no destination-balance fallback and no on-timeout/on-error re-credit, permanently burning claimed fees on revert - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
This is the closest local analog to the `swapIdleAndAddToLiquidity` bug class: an entrypoint takes a caller-supplied/derived "amount to move" and does not clamp it to the actual balance available at execution time, and unlike `SimplexPaymaster.swapAndDeposit` (which *does* clamp `amountIn` to `balanceOf(this)` before swapping, see `evm/src/utils/SimplexPaymaster.sol:306-307`), the relayer fee-withdrawal path does not, and it also permanently clears its internal accounting *before* the cross-chain payout is known to succeed.

### Finding Description
`Pallet::withdraw` in [1](#0-0)  reads `available_amount = Fees::<T>::get(dest_chain, address)`, dispatches an ISMP POST carrying that exact `amount` to the destination `host_manager` (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate), and then unconditionally zeroes `Fees` immediately after `dispatch_request` succeeds: [2](#0-1) 

`dispatch_request` only confirms the message was queued/dispatched from Hyperbridge — it says nothing about whether the destination `HostManager.withdraw` call will actually succeed. On the EVM side, `EvmHost.withdraw` performs a direct, unclamped transfer of `params.amount`: [3](#0-2) 

If the host's fee-token balance is lower than `available_amount` at delivery time (e.g., another relayer withdrew concurrently, or governance/`dispatch_withdraw`/`BandwidthManager` drained the same fee-token balance in the interim — accrued protocol revenue is not partitioned per-relayer on the EVM side), `IERC20.safeTransfer` reverts. This exact insufficient-balance failure mode is already exercised in tests: [4](#0-3) .

The critical difference from `swapAndDeposit`'s guard is that Hyperbridge's `Fees` storage is zeroed as soon as the outbound message is dispatched, not after confirmation of successful execution on the destination. I was unable to find any `on_timeout`/`on_response` handler in `modules/pallets/relayer/src/` that re-credits `Fees` when the destination call reverts or the request times out — a grep for `on_timeout`, `on_response`, and `IsmpModule` implementations in that pallet's source files returned no matches. Given the codebase's index limits, I could not fully confirm whether such a handler exists elsewhere (e.g., wired through the runtime rather than the pallet crate itself); this should be verified directly against the full repository.

If no such re-credit path exists, a relayer that legitimately accrued fees can have their `Fees` balance zeroed by their own withdrawal call while the actual payout reverts on the destination chain, permanently losing the claimed funds with no path to re-claim (their nonce has also already been incremented, so replaying the same signed withdrawal request is not straightforward either).

### Impact Explanation
This falls under "stealing or loss of funds" for the relayer reward/fee-claim flow: an unprivileged relayer's already-accrued reward balance can be wiped out without ever being paid, purely due to a benign balance race on the destination host (concurrent withdrawals, or the shared fee-token balance being drawn down by other flows), not due to any malicious actor.

### Likelihood Explanation
Medium: the fee token balance on `EvmHost` is a shared pool across all relayers claiming from that destination, and also intersects with the `BandwidthManager`/host-executive withdrawal paths that move the same token. Any timing where accrued balance dips below what's recorded as `available_amount` for a particular relayer (e.g., two relayers each holding large claims and withdrawing near-simultaneously) triggers the destination-side revert while the source-side `Fees` entry has already been zeroed.

### Recommendation
- Do not zero `Fees` optimistically before dispatch; zero it (or decrement it) only upon confirmed successful execution via an ISMP response/callback, and restore it on timeout/error responses.
- Alternatively, mirror `SimplexPaymaster.swapAndDeposit`'s pattern on the destination: have `EvmHost.withdraw` / `HostManager.onAccept` clamp `params.amount` to the actual on-chain balance (`min(amount, balanceOf(this))`) rather than reverting outright, and have Hyperbridge's `Fees` accounting be corrected/reconciled based on what was actually paid, reported back via an ISMP response.

### Proof of Concept
Conceptual reproduction (given tool-call limits, this reflects the code paths above, not an executed exploit):
1. Relayer R accrues `Fees[dest_chain][R] = X` via `accumulate.rs`.
2. Concurrently, another relayer or a governance `dispatch_withdraw`/`BandwidthManager.purchase` drains the shared EVM host fee-token balance below `X`.
3. R calls `Pallet::withdraw`; `available_amount = X` is read, the ISMP POST with `amount = X` is dispatched, and `Fees[dest_chain][R]` is immediately set to `0` per [5](#0-4) .
4. On delivery, `EvmHost.withdraw` calls `IERC20(token).safeTransfer(beneficiary, X)` which reverts because `balanceOf(host) < X`, per the exact scenario tested in [4](#0-3) .
5. R's fee entry is now `0` on Hyperbridge, the transfer never executed on the EVM side, and (unless a re-credit-on-timeout mechanism exists elsewhere in the runtime that I could not locate) R has permanently lost the claimed reward.

This warrants direct verification in a full checkout (via a Devin session) of whether `pallet-relayer` (or its runtime wiring) implements `on_timeout`/error-response handling that restores `Fees`, since the index available to me did not surface one.

### Citations

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

**File:** evm/src/core/EvmHost.sol (L651-660)
```text
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
    }
```

**File:** evm/tests/rust/src/tests/host_manager.rs (L143-172)
```rust
#[test]
fn test_host_manager_insufficient_balance() {
	let mut env = TestEnv::new();
	let manager = host_manager_of(&mut env);

	// Host has no fee tokens; withdraw attempt should fail on SafeERC20 transfer
	let params = WithdrawalParams {
		beneficiary_address: H160::random().as_bytes().to_vec(),
		amount: SubstrateU256::from(500_000_000_000_000_000_000u128),
		token: H160::from_slice(env.fee_token.as_slice()),
	};

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body: params.abi_encode().expect("20-byte beneficiary"),
	};
	let evm_request: EvmPostRequest = post.into();

	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	let err = env
		.call_as_may_revert(host_addr, manager, calldata)
		.expect_err("expected revert");
	assert!(!err.is_empty(), "expected non-empty revert data");
}
```
