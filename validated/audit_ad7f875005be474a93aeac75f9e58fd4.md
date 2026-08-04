## Analysis

The external report's core broken invariant is: **a value that controls fund recovery/refund on cross-chain fee handling is hardcoded to a value that cannot receive/authorize the refund, so genuine failures cause irrecoverable loss.** The direct Hyperbridge analog is in the relayer fee withdrawal flow.

### Title
Relayer fee balance is zeroed before destination delivery is confirmed, and the withdrawal dispatch nulls both `payer` and `fee` so a destination timeout/failure permanently burns the relayer's fees - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`Pallet::withdraw` in `modules/pallets/relayer/src/withdrawal.rs` debits a relayer's accumulated `Fees` balance to zero immediately after dispatching a cross-chain POST request that is supposed to instruct the destination chain's host manager (EVM) or hyperbridge module (substrate) to pay out `available_amount` to the beneficiary [1](#0-0) . The dispatch is made with `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }`, explicitly nulling the fields that the generic ISMP timeout-refund path (`EvmHost.dispatchTimeOut` / the pallet-side equivalent) uses to restore funds if the request times out [2](#0-1) .

### Finding Description
The generic Hyperbridge request lifecycle guarantees a refund of the recorded `fee` to the recorded `payer` if a dispatched POST request times out - this is the standard safety net documented for `dispatchTimeOut` [3](#0-2)  and mirrored for GET requests [4](#0-3) . This mechanism only works because the fee/payer metadata recorded at dispatch time is meaningful.

In `withdraw`, the pallet deliberately sets `payer: [0u8; 32].into()` and `fee: Default::default()` with the comment "Account is not useful in this case" [5](#0-4) . This is structurally identical to the reported bug's use of `address(this)` instead of a real, useful refund/cancel address — in both cases the field that governs recovery on failure is set to a value that provides no real recourse (a burn address / self address instead of the actual relayer/beneficiary).

Worse than the original report, here the pallet does not wait for confirmation at all: it zeroes the relayer's `Fees` entry unconditionally right after dispatch succeeds locally, before any destination-side execution or confirmation is known [6](#0-5) . If the destination request:
- times out (congestion, wrong `host_manager`/module binding, destination host frozen, chain reorg invalidating the proof window), or
- is delivered but the destination-side handler reverts for any reason (e.g., `WithdrawalParams` decode issues, an unregistered `fee_token`, or a paused host manager),

then per the standard Hyperbridge safety net the source chain should refund `fee` to `payer` on timeout. But since `fee = 0` and `payer = [0u8;32]`, the timeout path refunds nothing, to nobody. The relayer's real balance (`available_amount`, potentially large) was already wiped from `Fees` before the destination outcome was known, and there is no other code path in this pallet that restores it. This is confirmed by there being no `on_timeout`/`IsmpModule` handler in `modules/pallets/relayer/src/lib.rs` tied to `WithdrawRelayerFees`/`WithdrawalParams` that would credit the balance back.

### Impact Explanation
This is a direct loss-of-funds bug: a relayer who has legitimately earned fees calls `withdraw`, the on-chain accounting immediately treats the fees as spent, but the actual payout is contingent on a fallible cross-chain message that carries no recovery metadata. Any ordinary failure mode on the destination side (not requiring a malicious relayer, prover, or governance actor — just network conditions, timeout, or destination misconfiguration) results in the relayer's funds being permanently and unrecoverably lost, with no automated or even manual path to reissue them since the `Fees` storage was already zeroed and the dispatch's `FeeMetadata` cannot identify the original relayer for a refund.

### Likelihood Explanation
Request timeouts and destination-side delivery failures are routine, expected operational conditions in ISMP (this is precisely why the protocol has a `dispatchTimeOut`/refund mechanism at all). Because `withdraw` opts out of that mechanism by nulling `payer`/`fee`, every relayer who withdraws fees is exposed whenever a timeout or destination failure occurs on their withdrawal message — this requires no adversarial relayer, prover, or governance behavior, only a normal timeout.

### Recommendation
Do not zero `Fees::<T>` until destination-side delivery is confirmed (e.g., via a response/receipt acknowledgment), or alternatively populate `FeeMetadata` with a real, useful `payer` (the withdrawing relayer's account) so the standard timeout-refund path can restore `Fees` when the withdrawal request fails or times out. At minimum, implement an `on_timeout` handler for the withdrawal request's module id that re-credits `Fees::<T>::insert(dest_chain, address, available_amount)` on timeout.

### Proof of Concept
1. Relayer accrues `Fees[dest_chain][relayer] = X` via `accumulate_fees` [7](#0-6) .
2. Relayer calls `withdraw` with a valid signature; the pallet dispatches a POST request to the destination's host manager and immediately sets `Fees[dest_chain][relayer] = 0` [1](#0-0) .
3. The dispatched request has `payer = 0x00..00`, `fee = 0` [5](#0-4) .
4. Destination request times out or its handler reverts (e.g., transient congestion, `host_manager` param temporarily misconfigured, or fee token deactivated on `HostParams`).
5. Source-side timeout processing runs the standard refund: `if meta.fee != 0 { refund }` — since `meta.fee == 0`, nothing is refunded, and even if it were, it would go to the null address, not the relayer [3](#0-2) .
6. The relayer's `X` tokens worth of fees are permanently lost: no longer in `Fees::<T>`, never delivered to the beneficiary on the destination, and never refunded on the source.

### Citations

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

**File:** evm/src/core/EvmHost.sol (L856-877)
```text
    function dispatchTimeOut(
        GetRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external restrict(_hostParams.handler) {
        // replay protection
        delete _requestCommitments[commitment];
        (bool success,) = _bytesToAddress(timeout.request.from)
            .call(abi.encodeWithSelector(IApp.onGetTimeout.selector, timeout));

        if (!success) {
            // so that it can be retried
            _requestCommitments[commitment] = meta;
            return;
        }

        if (meta.fee != 0) {
            // refund relayer fee
            IERC20(feeToken()).safeTransfer(meta.sender, meta.fee);
        }
        emit GetRequestTimeoutHandled({commitment: commitment, dest: string(timeout.request.dest)});
    }
```

**File:** evm/src/core/EvmHost.sol (L885-906)
```text
    function dispatchTimeOut(
        PostRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external restrict(_hostParams.handler) {
        // replay protection
        delete _requestCommitments[commitment];
        (bool success,) = _bytesToAddress(timeout.request.from)
            .call(abi.encodeWithSelector(IApp.onPostRequestTimeout.selector, timeout));

        if (!success) {
            // so that it can be retried
            _requestCommitments[commitment] = meta;
            return;
        }

        if (meta.fee != 0) {
            // refund relayer fee
            IERC20(feeToken()).safeTransfer(meta.sender, meta.fee);
        }
        emit PostRequestTimeoutHandled({commitment: commitment, dest: string(timeout.request.dest)});
    }
```

**File:** modules/pallets/relayer/src/accumulate.rs (L134-147)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```
