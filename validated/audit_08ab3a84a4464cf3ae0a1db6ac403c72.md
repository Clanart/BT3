### Title
Relayer fee accounting is zeroed before destination-side settlement is confirmed, permanently losing accrued fees on withdrawal failure or timeout - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-ismp-relayer`'s `withdraw` function dispatches an ISMP POST request instructing the destination chain's `HostManager`/`EvmHost` to pay out a relayer's accrued fee, and then unconditionally zeroes the local `Fees` ledger entry on Hyperbridge in the same call, without waiting for confirmation that the destination-side payout actually succeeded. This mirrors the `[H-02]` pattern in the external report — a "kill"-like state transition (in the Voter contract, `claimable[_gauge] = 0`) that irreversibly wipes an accounting balance with no mechanism to recover it if the corresponding downstream effect (gauge revival / cross-chain payout) does not complete as expected.

### Finding Description
`Pallet::<T>::withdraw` at [1](#0-0)  reads the relayer's accrued balance via `Fees::<T>::get`, checks it against the minimum withdrawal threshold, then dispatches an ISMP `DispatchPost` to the destination's `HostManager` (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate) carrying a `WithdrawalParams`/`WithdrawalRequest` payload instructing it to pay `available_amount` to the beneficiary. Immediately after `dispatcher.dispatch_request(...)` returns `Ok`, the code does:

```rust
Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
``` [2](#0-1) 

`dispatch_request` only confirms that the ISMP message was *accepted for dispatch* on Hyperbridge — it says nothing about whether the destination chain will actually execute the payout. The destination-side execution can fail or never land for several protocol-legitimate reasons that are not attacker-controlled:
- The request can time out (the `DispatchPost.timeout` is `0`, i.e. no timeout is even enforced defensively, but a destination host manager can still fail to process it, e.g. it's frozen (`EvmHost.setFrozenState`, see [3](#0-2)  — while `FrozenStatus.Incoming` is active, this and other incoming dispatches are rejected).
- The `HostManager`'s balance of the fee token can be insufficient, or the ERC20 transfer/call can revert (analogous to `InsufficientNativeToken` reverts seen in `BandwidthManager.onAccept`'s `Withdraw` handling, [4](#0-3) , which is the same code shape the relayer withdrawal reuses via `pallet-ismp-host-executive`).

In all of these cases, the relayer's `Fees` entry on Hyperbridge has already been permanently reset to zero at line 177, exactly as `killGauge` permanently zeroes `claimable[_gauge]` in the referenced report regardless of whether the gauge is later revived. There is no on-chain mechanism observed in `pallet-ismp-relayer` to re-credit `Fees` if the destination-side settlement fails, times out, or the request is later found undelivered — the accounting state and the actual cross-chain fund movement are decoupled and irreversible once the local zeroing happens.

### Impact Explanation
This causes **permanent loss of relayer-accrued rewards** — real economic value that a relayer legitimately earned for delivering cross-chain messages — whenever the corresponding destination-chain payout does not complete. This falls squarely within the bounty's "stealing or loss of funds" category: funds intended for a rightful beneficiary (the relayer) can become permanently unclaimable through no fault or malicious behavior of the relayer, purely due to a normal failure mode on the destination chain (host frozen, insufficient host-manager balance, or a reverted transfer).

### Likelihood Explanation
Likelihood is Medium: this requires a benign destination-side failure condition (host manager temporarily frozen or under-funded) coinciding with a legitimate relayer withdrawal — not any attacker action, malicious relayer, or admin/governance error. Both conditions (host freezing and host-manager balance shortfalls) are ordinary operational states the protocol code documents as expected occurrences (see `setFrozenState`/`FrozenStatus.Incoming` and the `HostManager`/`BandwidthManager` fee-token draining/insufficiency error paths).

### Recommendation
Do not zero the `Fees` entry until destination-side settlement is confirmed. Options:
1. Move the `Fees::<T>::insert(..., U256::zero())` to an `on_response`/delivery-confirmation callback keyed by the dispatch commitment, so the ledger is only cleared once the destination chain proves it processed the withdrawal.
2. Alternatively, keep a pending-withdrawal record keyed by commitment, and if a timeout is observed for that commitment (ISMP already supports timeout callbacks), re-credit `Fees` for the affected relayer/chain pair.
3. At minimum, add a reconciliation path (analogous to the recommendation in the H-02 report) that lets an accrued-but-unsettled amount be recovered/re-claimed if the destination confirms non-execution.

### Proof of Concept
Conceptual sequence (cannot be executed without a full testnet, but demonstrates the exact code path):
1. Relayer accumulates fees via `accumulate_fees`, crediting `Fees::<T>::get(dest_chain, relayer) = X` (`modules/pallets/relayer/src/accumulate.rs:134-147`).
2. Destination `EvmHost` for `dest_chain` is frozen (`setFrozenState(FrozenStatus.Incoming)`) or its `HostManager`/fee-token balance is insufficient, either due to normal operations (admin/handler routine freeze during an upgrade window, or the host-manager's fee-token balance temporarily drained by a legitimate `dispatch_withdraw` governance call, see `evm/src/apps/BandwidthManager.sol:213-221` for the analogous revert path on withdrawal).
3. Relayer calls `withdraw_fees` → `Pallet::<T>::withdraw`. `dispatch_request` succeeds locally (message is queued for the destination), and `Fees::<T>::insert(dest_chain, relayer, 0)` executes unconditionally at `modules/pallets/relayer/src/withdrawal.rs:177`.
4. The dispatched request subsequently fails or is dropped on `dest_chain` (frozen host, revert on transfer). The relayer never receives `X`, and `Fees[dest_chain][relayer]` is now `0` with no code path to restore it.

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

**File:** evm/src/core/EvmHost.sol (L746-753)
```text
    function setFrozenState(FrozenStatus newState) external {
        address caller = _msgSender();
        if (caller != _hostParams.admin && caller != _hostParams.handler) revert UnauthorizedAction();

        _frozen = newState;

        emit HostFrozen({status: newState});
    }
```

**File:** evm/src/apps/BandwidthManager.sol (L213-221)
```text
        } else if (action == OnAcceptActions.Withdraw) {
            Withdrawal memory w = abi.decode(request.body[1:], (Withdrawal));
            if (w.token != address(0)) {
                IERC20(w.token).safeTransfer(w.beneficiary, w.amount);
            } else {
                (bool sent,) = w.beneficiary.call{value: w.amount}("");
                if (!sent) revert InsufficientNativeToken();
            }
            emit Withdrawn(w.token, w.beneficiary, w.amount);
```
