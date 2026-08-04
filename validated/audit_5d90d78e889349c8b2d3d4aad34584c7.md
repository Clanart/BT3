### Title
Relayer fee balance is zeroed before payout settles, permanently stranding funds on failed/undelivered withdrawal - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`pallet-ismp-relayer`'s `Pallet::withdraw` reads the relayer's accrued `Fees` balance, dispatches a cross-chain POST request instructing the destination chain to pay it out, and then unconditionally zeroes the `Fees` entry — all before there is any confirmation that the payout message was ever delivered or executed successfully. This mirrors the `BaseGauge` bug class: an accounting entry ("obligation owed") is cleared based on an *intent* to pay rather than a *confirmed* payment, and the actual balance/settlement on the paying side can independently fail, leaving the beneficiary with nothing and no way to reclaim it.

### Finding Description
In `Pallet::withdraw` [1](#0-0) , the flow is:

1. Read `available_amount = Fees::<T>::get(dest_chain, address)`.
2. Build a `DispatchPost` targeting either the destination's `HostManager` (EVM) or the substrate `HYPERBRIDGE_MODULE_ID`, embedding `available_amount` in the body.
3. Dispatch the request via `dispatcher.dispatch_request(..., FeeMetadata { payer: [0u8;32].into(), fee: Default::default() })` — note the **relayer fee attached to this delivery message itself is zero**.
4. Immediately after dispatch succeeds (i.e., the message is merely *queued*, not delivered), `Fees::<T>::insert(dest_chain, address, U256::zero())` unconditionally zeroes the accounting entry.

The dispatched request uses `timeout: 0`, which per the withdrawal module's own doc comment "will not timeout, allowing it to be submitted to the destination chain at any time" [2](#0-1) . Because it never times out, there is no timeout-triggered refund path (`on_request_timeout`) to restore the zeroed `Fees` entry if delivery never happens.

On the EVM side, the actual fund movement happens inside `HostManager.onAccept` → `EvmHost.withdraw`, which performs `IERC20(params.token).safeTransfer(params.beneficiary, params.amount)` [3](#0-2) . This reverts if the `EvmHost` contract does not hold sufficient fee-token balance — a condition explicitly exercised by the test suite: [4](#0-3) .

Because the message carries zero relayer fee, and the docs state relayers "are profit-driven mediators and they will prioritize messages with fees that ensure profitability" [5](#0-4) , there is no economic incentive for any relayer to actually deliver this specific message. Combined with the demonstrated revert-on-insufficient-balance path, the withdrawal message can remain permanently undelivered or repeatedly revert on execution, while the `Fees` ledger entry that represented the relayer's claim on Hyperbridge has already been zeroed at step 4, with no compensating credit-back logic anywhere in `withdrawal.rs`, `accumulate.rs`, or `dispatcher.rs`.

### Impact Explanation
This is a direct loss-of-funds path for relayers: the on-chain record of what they are owed (`Fees<T>`) is cleared optimistically at request time rather than at confirmed settlement time. If the destination-side transfer reverts (starved `EvmHost` fee-token balance) or the zero-fee message is simply never picked up by any relayer for delivery, the relayer's accrued, previously-proven revenue is irrecoverably lost with no retry, refund, or re-credit mechanism — the same broken invariant the external report flags (bookkeeping of "amount owed" diverging from what actually gets paid out).

### Likelihood Explanation
The precondition (destination host underfunded relative to accrued relayer claims) is realistic in production: relayer fees on EVM hosts are funded by ordinary application fee payments into `EvmHost`, and there is no cross-chain synchronization guaranteeing the destination's fee-token balance always covers Hyperbridge's `Fees<T>` ledger before a withdrawal is issued. Any unprivileged relayer triggering `withdraw_fees` (an unsigned, permissionless extrinsic per `withdrawal.rs`) can experience this without any malicious actor involved — it requires no compromised relayer, prover, or admin, satisfying the bounty's "no privileged actor" requirement.

### Recommendation
- Do not zero `Fees<T>` at dispatch time. Instead, keep the balance "reserved/pending" and only clear it once a corresponding delivery/settlement receipt is proven back to Hyperbridge (mirroring the `accumulate.rs` claimed-flag pattern that only mutates state after proof verification).
- Attach a non-zero relayer fee to the withdrawal-delivery message itself, or otherwise guarantee its delivery, so it cannot silently stall.
- Add a re-credit/refund path (analogous to `on_request_timeout`) for withdrawal requests that fail to execute on the destination, so a reverted `EvmHost.withdraw` restores the relayer's claimed balance instead of discarding it.

### Proof of Concept
1. Relayer accrues fees on Hyperbridge via `accumulate_fees`, giving `Fees::<T>::get(Evm(1), relayer) = 500 tokens` (see test pattern in `modules/pallets/testsuite/src/tests/pallet_ismp_relayer.rs:227-269`).
2. Assume `EvmHost` on chain `Evm(1)` currently holds less than 500 fee tokens (e.g., due to prior withdrawals or fee-token migration).
3. Relayer calls `withdraw_fees` → `Pallet::withdraw`. This dispatches the `WithdrawalParams{amount: 500}` POST to `HostManager` with zero attached relayer fee, then immediately sets `Fees::<T>::insert(Evm(1), relayer, 0)` [6](#0-5) .
4. When/if a relayer eventually delivers the message, `HostManager.onAccept` calls `EvmHost.withdraw`, which reverts via `safeTransfer` due to insufficient balance — exactly the condition proven in `test_host_manager_insufficient_balance` [4](#0-3) .
5. Delivery fails (or, since the message pays no fee, is never attempted at all). The relayer's `Fees` entry is already 0 on Hyperbridge — the 500 tokens they legitimately earned are unrecoverable.

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

**File:** docs/content/developers/polkadot/fees.mdx (L19-23)
```text
| Component | Description |
|-----------|-------------|
| **Proof verification cost** | For a cross-chain message to be delivered and executed, it must first be authenticated through state proofs. The expected cost for state proof verification on EVM chains is ~150k gas. Modules should account for this cost when setting the relayer fee. |
| **Message execution gas cost** | After proof verification, the receiving module is handed the request to be executed. This will consume some gas which should also be accounted for. |
| **Relayer service fee** | This additional amount rewards relayers for their services. Relayers are profit-driven mediators and they will prioritize messages with fees that ensure profitability. |
```
