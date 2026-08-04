The strongest local analog to the Lybra `ProtocolRewardsPool` bug is the relayer fee withdrawal flow in `pallet-relayer`, which zeroes the on-chain reward balance *before* confirming the destination-side payout actually succeeds — the same "balance-vs-owed-amount" mismatch class as the original finding, but here it produces permanent loss rather than a revert.

### Title
Relayer fee balance is zeroed before destination payout is confirmed, permanently losing rewards on insufficient host-manager balance - (File: modules/pallets/relayer/src/withdrawal.rs)

### Summary
`Pallet::withdraw` reads the relayer's accrued `Fees` balance, dispatches an ISMP POST instructing the destination chain's host/manager to disburse that amount, and unconditionally zeroes `Fees` for that `(dest_chain, relayer)` key immediately after the dispatch call succeeds — not after the destination confirms the transfer landed.

### Finding Description
In `Pallet::withdraw`: [1](#0-0) 
the relayer's `available_amount` is read from `Fees`, and after building/dispatching the withdrawal POST request: [2](#0-1) 
`Fees::<T>::insert(..., U256::zero())` runs right after `dispatch_request` succeeds — i.e. after Hyperbridge only knows the *request was queued*, not that the destination-side transfer executed successfully.

The destination-side execution path (EVM) is `HostManager.onAccept` → `EvmHost.withdraw`: [3](#0-2) [4](#0-3) 
which performs `IERC20(token).safeTransfer(beneficiary, amount)` (or a raw ETH `.call`). This reverts if the host's fee-token balance is insufficient — confirmed directly by the test suite: [5](#0-4) 

The dispatched POST uses `timeout: 0` (never expires), so there is no timeout-driven refund path back into `Fees` if the destination-side call reverts. Once `Fees` is zeroed on Hyperbridge, there is no mechanism to re-credit the relayer if the corresponding on-chain transfer never lands (host manager under-funded, fee token paused, blacklisted beneficiary, or another relayer's earlier withdrawal already drained the shared host-manager balance).

This is structurally the same defect as the Lybra `ProtocolRewardsPool.getReward` bug: the ledger accounting ("what's owed") is mutated based on an amount that assumes the payout will succeed, without first checking or reserving against the actual destination-side balance that will service the payout.

### Impact Explanation
A relayer's legitimately accrued protocol fee reward can become permanently unclaimable / lost with no recovery path, once the corresponding host-manager/EvmHost balance is insufficient at delivery time. This is a direct "loss of funds" outcome for an honest, unprivileged party (the relayer), matching the bounty's accepted impact class.

### Likelihood Explanation
No malicious peer, relayer, or governance action is required. This can be triggered by ordinary operational conditions: multiple relayers withdrawing concurrently against the same shared EvmHost fee-token pool (a race the code has no reservation/locking against), or the host-manager's balance being temporarily below what's owed. The withdrawal call itself is reachable by any relayer with valid nonce/signature, per `Pallet::withdraw`.

### Recommendation
Do not zero `Fees` synchronously with dispatch. Either:
- Zero the balance only after an on-chain confirmation/receipt of successful destination-side execution (e.g. via an ISMP response/ack), or
- Keep the dispatched request retryable and re-credit `Fees` on a delivery failure/timeout, or
- Reserve/lock the amount at dispatch time and only finalize (permanently deduct) once the destination transfer is confirmed successful, refunding the reservation on failure.

### Proof of Concept
1. Relayer R accrues fees on Hyperbridge for `dest_chain = Evm(1)`; `Fees[Evm(1)][R] = X`.
2. `EvmHost`'s fee-token balance on `Evm(1)` is currently `< X` (e.g., drained by a prior withdrawal from another relayer, or fees not yet swept in).
3. R calls `withdraw_fees` → `Pallet::withdraw` succeeds locally: `dispatch_request` queues the POST, and `Fees[Evm(1)][R]` is set to `0` (`modules/pallets/relayer/src/withdrawal.rs:177`).
4. The POST is delivered to `HostManager.onAccept` → `EvmHost.withdraw` → `IERC20.safeTransfer(R, X)` reverts because the balance is insufficient (exact scenario reproduced by `test_host_manager_insufficient_balance`, `evm/tests/rust/src/tests/host_manager.rs:143-172`).
5. The transfer never lands. `timeout: 0` means no timeout ever fires to trigger a refund. `Fees[Evm(1)][R]` is already `0` on Hyperbridge — R's reward `X` is permanently lost with no retry or reclaim path.

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

**File:** modules/pallets/relayer/src/withdrawal.rs (L169-184)
```rust
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

**File:** evm/src/core/HostManager.sol (L95-104)
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
