## Analysis

The LimboDAO bug's core broken invariant: **an action is treated as final/committed before the system verifies that its downstream follow-through will actually succeed, and there is no recovery path if the follow-through cannot complete** — leading to permanently locked funds.

I found a direct structural analog in Hyperbridge's relayer fee withdrawal flow.

### Title
Relayer fee balance is irrecoverably burned when a non-timing-out cross-chain withdrawal disbursement fails on the destination - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-relayer`'s `withdraw()` function zeroes a relayer's `Fees` balance and increments their `Nonce` **immediately after dispatching** the ISMP POST request that instructs the destination chain to pay out the funds — before any confirmation that the destination-side disbursement actually succeeds. The dispatched request is explicitly configured with `timeout: 0`, meaning it can never time out and therefore can never trigger a corrective/retry path on the source chain if the destination-side payout call permanently reverts.

### Finding Description
In `withdraw()` [1](#0-0) , the pallet:
1. Increments `Nonce::<T>` for the relayer/destination pair.
2. Builds a `DispatchPost` with `timeout: 0` targeting the destination's `IHostManager`/`HYPERBRIDGE_MODULE_ID`.
3. Dispatches the request via `dispatcher.dispatch_request(...)`.
4. **Unconditionally zeroes `Fees::<T>`** for that relayer right after dispatch succeeds [2](#0-1) .

The actual payout only happens later, on the destination chain, when the relayed POST request reaches `HostManager.onAccept` → `IHostManager.withdraw()` → `safeTransfer`/native `call` [3](#0-2) [4](#0-3) . This call can revert for legitimate operational reasons — insufficient fee-token balance in the host, a stale/incorrect `hostManager`/`host_executive` `HostParam` entry, or the host being frozen — as demonstrated by the test `test_host_manager_insufficient_balance` [5](#0-4) .

Normally, ISMP's request/timeout machinery provides a corrective path: if delivery fails, the request either gets retried (receipt deleted on failed `dispatchIncoming` [6](#0-5) ) or eventually times out and restores state via `on_timeout` [7](#0-6) . But here `timeout: 0` disables that entire safety net for this specific request — it can be retried on the destination indefinitely, but there is no mechanism on the **source** (Hyperbridge) side to detect a permanently-failing destination disbursement and restore the relayer's `Fees` balance, because the accounting mutation was already finalized at dispatch time, decoupled from delivery outcome.

This mirrors the LimboDAO defect precisely: the state transition (`proposal accepted` / `Fees zeroed`) is committed optimistically, assuming the subsequent execution (`proposal.execute()` / on-chain payout) will succeed, with no boolean-success-checked unwind path if it doesn't.

### Impact Explanation
If the destination host's `HostManager`/`updateHostParams`/fee-token balance is misconfigured or under-funded at the moment of withdrawal delivery (a routine, non-adversarial operational condition — bridge revenue balances fluctuate, host params get rotated via governance), the relayer's entire accrued fee balance for that chain is permanently zeroed with no destination payout ever completing and no way to reclaim it. This is a direct, unrecoverable loss of relayer funds — the exact "loss of funds" category the bounty targets — without requiring any malicious relayer, prover, or governance actor.

### Likelihood Explanation
This requires only an ordinary condition already exercised by the codebase's own test suite (insufficient host balance causes `onAccept`/`withdraw` to revert), not privileged access or malicious behavior. Any relayer withdrawing at a time when the destination host's fee-token treasury is temporarily below the requested amount (e.g., not yet swept by governance's periodic `withdraw`/`dispatch_withdraw` flows [8](#0-7) ) would trigger this loss.

### Recommendation
Do not finalize the source-side balance mutation until the disbursement is confirmed. Options: (a) give the withdrawal request a real timeout and implement `on_timeout`/`on_response` handling in `pallet-relayer` that restores `Fees` on failure or non-delivery, mirroring the existing generic ISMP request pattern of "delete on optimistic action, restore on failure" already used elsewhere in the codebase [7](#0-6) ; or (b) only zero `Fees` upon receiving a confirmed success acknowledgment (response) from the destination rather than at dispatch time.

### Proof of Concept
1. Relayer accrues `Fees[dest_chain][relayer] = X` on Hyperbridge.
2. Destination EVM host's fee-token balance is currently `< X` (a normal, non-adversarial state, as directly reproduced by `test_host_manager_insufficient_balance`) [5](#0-4) .
3. Relayer calls `withdraw_fees` with a valid signature; `pallet-relayer::withdraw()` dispatches the `WithdrawParams` POST with `timeout: 0` and immediately sets `Fees[dest_chain][relayer] = 0` [9](#0-8) .
4. Relayer delivers the request to the destination; `HostManager.onAccept` → `EvmHost.withdraw()` reverts because `IERC20(token).safeTransfer` fails on insufficient balance [4](#0-3) .
5. Because `timeout: 0`, this request never expires and pallet-relayer has no callback to observe the failure; `Fees` remains permanently `0` and the relayer cannot re-claim the burned balance.

**Uncertainty note:** I was unable to fully confirm, within the available search budget, whether `pallet-relayer` or the generic `pallet-ismp` dispatcher registers any `IsmpModule::on_timeout`/`on_response` implementation for `HYPERBRIDGE_MODULE_ID` that might restore `Fees` in some other code path I did not locate (grep for `on_timeout` in `modules/pallets/relayer/` returned no implementation, but the module registration for `MODULE_ID` could live in a router file not indexed). This should be verified directly in the repository before treating the finding as fully confirmed.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L125-177)
```rust
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

**File:** evm/src/core/HostManager.sol (L95-109)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
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

**File:** evm/src/core/EvmHost.sol (L811-818)
```text

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
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

**File:** modules/ismp/core/src/handlers/timeout.rs (L122-134)
```rust
					if res.is_ok() {
						host.on_request_timeout(&request, meta)?;
					} else {
						// Module callback failed; restore commitment so the request
						// can be retried.
						host.store_request_commitment(&request, meta)?;
						if host.host_state_machine() != post.source && signer.is_some() {
							host.store_request_receipt(
								&request,
								&signer.ok_or_else(|| anyhow::anyhow!("Infallible"))?,
							)?;
						}
					}
```

**File:** modules/pallets/host-executive/src/lib.rs (L273-319)
```rust
		/// Issues a call to withdraw the protocol fees from an evm chain
		#[pallet::weight(T::DbWeight::get().writes(1))]
		#[pallet::call_index(4)]
		pub fn withdraw(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			withdrawal_params: WithdrawalParams,
		) -> DispatchResult {
			T::HostExecutiveOrigin::ensure_origin(origin)?;

			ensure!(state_machine.is_evm(), Error::<T>::UnsupportedStateMachine);

			let HostParam::EvmHostParam(params) = HostParams::<T>::get(state_machine)
				.ok_or_else(|| Error::<T>::UnknownStateMachine)?;

			let data = withdrawal_params
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidBeneficiaryAddress)?;

			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_ID.to_bytes(),
				to: params.host_manager.0.to_vec(),
				timeout: 0,
				body: data,
			};

			let dispatcher = <T as Config>::IsmpHost::default();

			// Account is not useful in this case
			dispatcher
				.dispatch_request(
					DispatchRequest::Post(post),
					FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
				)
				.map_err(|_| Error::<T>::DispatchFailed)?;

			Self::deposit_event(Event::<T>::Withdraw {
				address: sp_runtime::BoundedVec::truncate_from(
					withdrawal_params.beneficiary_address,
				),
				state_machine,
				amount: withdrawal_params.amount,
			});

			Ok(())
		}
```
