## Finding: Relayer fee balance is zeroed before destination payout is confirmed, permanently losing funds on delivery failure

### Title
Relayer fee balance is irrevocably zeroed before destination-chain disbursement is confirmed, causing permanent loss on failed withdrawal delivery - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
The `Fees` storage in `pallet-ismp-relayer` (the on-chain "reserve" of what a relayer is owed) is set to zero the instant the withdrawal is dispatched, not after the destination-chain payout has been proven successful. This is the same broken invariant as the ubet `ParentFundingPool` bug: an accounting value that represents "amount owed" is updated to reflect a payment that has not actually been confirmed to have landed, so if the destination-side execution fails or under-delivers, the shortfall is permanently lost with no path to reclaim it.

### Finding Description
`Pallet::withdraw` in [1](#0-0)  reads the relayer's full accrued balance (`available_amount = Fees::<T>::get(...)`), builds a `DispatchPost` instructing the destination's `HostManager` (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate) to disburse that amount, dispatches it with `timeout: 0` (never times out, so there is no automatic refund/retry signal), and then immediately does `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero())` — zeroing the balance unconditionally, before any state proof of delivery or successful execution is ever observed.

On the EVM side, the actual payout is executed by `HostManager.onAccept` at [2](#0-1)  which calls `IHostManager(_params.host).withdraw(withdrawParams)` on `EvmHost`. If that withdrawal call reverts for any reason (e.g., the host's fee-token reserves are insufficient to cover the full `available_amount`, a paused/blacklisted token, or any other execution failure on the destination), the ISMP request execution fails on the destination chain. Because the source-side `Fees` entry was already zeroed at dispatch time and the request carries `timeout: 0`, there is no compensating mechanism on hyperbridge to re-credit the relayer — the fee is gone.

This mirrors the external report's exact broken invariant: the ubet pool set `valueHighPoint = poolValue - fees` using the *intended* fee amount rather than the amount actually collectable, losing the difference when reserves were short. Here, `Fees` is zeroed based on the *intended* payout amount rather than a confirmed, proven disbursement, losing the relayer's reward entirely if the destination execution can't or doesn't complete.

### Impact Explanation
This is a direct loss-of-funds bug for relayers, who are the parties Hyperbridge is economically obligated to pay for delivering cross-chain messages. Any transient or permanent failure on the destination side of a withdrawal (insufficient reserve token balance in the `EvmHost`/`HostManager` contract, a reverting fee-token transfer, governance pausing the token, etc.) after the source-side ledger has already been zeroed results in silent, unrecoverable loss of the relayer's accrued fees. Given `pallet-ismp-relayer` is the module tracking and paying out relayer incentives across the entire protocol, this directly undermines the relayer incentive model that keeps message delivery running.

### Likelihood Explanation
This does not require a malicious actor, relayer collusion, or governance compromise — it is triggered by ordinary operational conditions: the destination host contract simply not holding enough of the fee token to cover a large accrued balance, a paused/blacklisted stablecoin, or any other legitimate revert during `IHostManager.withdraw`. Any relayer who accumulates a balance larger than the current reserve on some destination chain, or who withdraws at a moment when the host manager's balance is temporarily depleted, will trigger this path. Because withdrawals are unsigned/open extrinsics (`ensure_none(origin)`) callable by any relayer holding a valid signature over their own fees at any time, this is easily reachable in normal operation.

### Recommendation
Do not zero (or move) `Fees` at dispatch time. Instead:
- Move the debit of `Fees` to occur only after a state proof confirms the destination-side disbursement executed successfully (mirroring the `accumulate_fees` pattern that already requires a proven delivery receipt before crediting), or
- Keep a separate "in-flight withdrawal" amount, and only permanently subtract from `Fees` once success is proven; on failure/timeout, restore the amount back into `Fees`.
- Alternatively, set a non-zero `timeout` on the dispatched request so failure/timeout can be detected and used to trigger a restore of the `Fees` balance.

### Proof of Concept
1. Relayer accrues `Fees[dest_chain][relayer] = 10_000` via `accumulate_fees`.
2. The `EvmHost`/`HostManager` on `dest_chain` currently holds less than `10_000` of the fee token in reserve (e.g. due to normal outflows/other withdrawals draining it, or governance pausing the token).
3. Relayer calls `withdraw_fees` with a valid signature. `withdrawal.rs` reads `available_amount = 10_000`, dispatches the ISMP POST to `HostManager` with `timeout: 0`, then immediately sets `Fees[dest_chain][relayer] = 0` at [3](#0-2) .
4. On `dest_chain`, `HostManager.onAccept` calls `EvmHost.withdraw(...)`, which reverts because the reserve balance is insufficient to transfer `10_000`. The request execution fails on the destination.
5. Because `timeout` was `0`, there is no timeout callback on hyperbridge to restore the balance, and `Fees` is already `0`. The relayer's entire `10_000` is permanently lost with no recovery path.

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
