## Analysis

The Frankencoin report's core broken invariant: a single state mutation (`cooldown = expiration`) forecloses the only exit path for an asset with **no compensating action if the expected outcome never materializes**, and there is no fallback/timeout mechanism to recover the asset.

The closest local analog in Hyperbridge is in `pallet-relayer`'s fee withdrawal flow, where the relayer's on-chain fee balance is zeroed **before** the cross-chain disbursement is confirmed, and the dispatched request is built with `timeout: 0` — meaning it can never time out and trigger any recovery.

### Title
Relayer fee balance is zeroed before cross-chain disbursement is confirmed, with no timeout or fallback recovery path - (`modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` reads the relayer's accumulated `Fees` balance, dispatches an ISMP POST request instructing the destination chain (EVM `HostManager`/`EvmHost.withdraw`, or the Substrate `HYPERBRIDGE_MODULE_ID`) to pay it out, and then unconditionally zeroes the local `Fees` entry — all within the same extrinsic, based only on the local dispatch call succeeding, not on any confirmation that the destination-side payout actually occurred.

### Finding Description
In `withdraw`, the balance is fetched into `available_amount` [1](#0-0) , a `DispatchPost` is built with `timeout: 0` [2](#0-1) , and the request is handed to `dispatcher.dispatch_request(...)`, whose only failure check is the local queuing call itself [3](#0-2) . Immediately afterward, `Fees` is zeroed for that relayer/chain pair regardless of what happens to the message downstream [4](#0-3) .

Because `timeout` is hardcoded to `0`, the ISMP request can never expire, so there is no `on_timeout` callback path in this module that could restore the zeroed balance if delivery never completes. If the request is never relayed, or if it is relayed but the destination-side handler cannot execute it (e.g., the EVM `HostManager.onAccept` → `EvmHost.withdraw` call reverts due to `params.fee_token` mismatch, insufficient host balance, or stale `HostParams` fetched at `HostParams::<T>::get(dest_chain)` [5](#0-4)  vs. what is later configured on-chain), the funds are never actually disbursed to the relayer, yet the source-chain accounting has already been permanently erased. This mirrors the reported pattern precisely: the "cooldown"-equivalent (irreversible `Fees` zeroing) is applied unconditionally on the assumption that the follow-up action (destination payout) will succeed, with no code path to reverse or retry it if it does not.

By contrast, the Intent Gateway's escrow design keeps `_orders[commitment][token]` balances in place until the actual `_withdraw` call executes on the chain holding the tokens [6](#0-5) , which is the safe pattern this relayer-fee flow deviates from.

### Impact Explanation
If the dispatched `WithdrawRelayerFees`/`WithdrawalParams` message is dropped, misrouted, or reverts irrecoverably on the destination side, the relayer's accumulated bridging fees are permanently lost — no other extrinsic or contract path re-credits `Fees` or reissues the payout. This is a direct loss-of-funds condition for an unprivileged, legitimate protocol participant (the relayer), matching the bounty's "stealing or loss of funds" category.

### Likelihood Explanation
Triggering the loss does not require a malicious relayer, prover, or admin — any relayer calling `withdraw` is exposed if the destination-side host params are stale/misconfigured relative to when the message is processed, or if the request is simply never relayed (self-relay is possible everywhere else in the codebase, e.g. intent-gateway cancels, but no self-relay/retry path is exposed for this specific message type once `Fees` is zeroed). The `timeout: 0` choice removes the protocol's normal safety net (timeout-triggered refund) that is used elsewhere in the codebase (e.g., `onPostRequestTimeout` refund handlers in `WrappedHyperFungibleToken.sol` [7](#0-6) ), making this pallet an outlier without a corresponding recovery mechanism. I could not fully verify from available code whether a separate governance-only recovery/reissue extrinsic exists elsewhere in the runtime; this should be confirmed before treating the finding as fully conclusive.

### Recommendation
Do not zero `Fees` until the destination chain confirms actual disbursement (e.g., via a response/ack message back to `pallet-relayer`), or alternatively set a non-zero `timeout` on the dispatched `DispatchPost` and implement an `on_timeout` handler that re-credits `Fees::<T>` for the relayer if the payout message expires undelivered.

### Proof of Concept
1. Relayer accumulates fees on `StateMachine::Evm(X)`, `Fees::<T>::get(chain, relayer) = 1000`.
2. Relayer calls `withdraw` with a valid signature; `Nonce` is incremented, a `DispatchPost{ timeout: 0, ... }` is queued, and `Fees` is zeroed in the same call [8](#0-7) .
3. Suppose the destination EVM chain's `HostParams::<T>::get` snapshot used to build `to`/`body` targets a `host_manager`/`fee_token` that no longer matches the live `EvmHost` configuration (updated via `UpdateParams` governance between dispatch and delivery), or the relayer simply never submits the message for delivery.
4. `HostManager.onAccept` either never executes or reverts every time it is attempted, so `EvmHost.withdraw` [9](#0-8)  never runs.
5. Because `timeout: 0` means the request can never expire, there is no automatic path back to `pallet-relayer` to restore the balance — the 1000 units of fees are permanently unrecoverable, with `Fees` already at zero.

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

**File:** modules/pallets/relayer/src/withdrawal.rs (L144-158)
```rust
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
```

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/src/core/EvmHost.sol (L647-660)
```text
    /**
     * @dev withdraws host revenue to the given address, can only be called by cross-chain governance
     * @param params, the parameters for withdrawal
     */
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
