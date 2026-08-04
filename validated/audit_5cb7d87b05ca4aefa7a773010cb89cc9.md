## Core broken invariant (from the report)

The Pool.sol bug is: **an accounted liability (`guaranteedValue`, driven by unprivileged trader borrowing) is unbounded relative to the physically available liquid balance (`poolAmount`)**, and withdrawal is dispatched against the accounted value rather than the real one, so ordinary/legitimate activity by unprivileged actors can permanently strand another actor's entitled funds.

## Local Hyperbridge analog

`pallet-ismp-relayer`'s fee accounting has the same shape. Relayer rewards are collected from a **single shared liquidity pool** — the source `EvmHost`'s `feeToken()` balance — which is credited by every dispatcher paying `post.fee` on `dispatch()`: [1](#0-0) 

That same pool is drawn down by two other **permissionless** paths that any relayer/user can trigger legitimately, with no coordination with relayer withdrawals:
- Timeout refunds pay `meta.fee` back to the original sender out of the same `feeToken()` balance, triggered by whoever submits a valid timeout proof: [2](#0-1) 
- Governance protocol-fee sweeps via `HostManager`/`EvmHost.withdraw()` (privileged, but draws the identical balance): [3](#0-2) 

On the Hyperbridge side, a relayer's entitlement is tracked as a purely **virtual accounting value** in `Fees<T>`, populated by `accumulate_fees` after proving delivery: [4](#0-3) 

When a relayer withdraws, `withdraw()` reads `available_amount = Fees::<T>::get(...)` and dispatches an ISMP request to the `HostManager` to disburse that amount from the shared pool — the pallet's own documentation states the ledger entry is zeroed immediately (optimistically) and the request is fire-and-forget with **no timeout**: [5](#0-4) [6](#0-5) 

## Why existing guards don't stop the path

Nothing enforces that `sum(Fees<T>[chain, *])` (the accounted liability, analogous to `guaranteedValue`) stays ≤ the real `feeToken()` balance sitting in that chain's `EvmHost` (analogous to `poolAmount`). Multiple relayers can legitimately accumulate real, earned fees against the same source-chain pool; any of them (or ordinary users triggering timeout refunds) can drain the physical balance before a given relayer's `withdraw_fees` ISMP message lands. Because:
1. The Hyperbridge-side `Fees<T>` entry is zeroed before the destination-side transfer executes, and
2. The withdrawal `PostRequest` is dispatched with `timeout: 0` — "This request will not timeout" —

a relayer whose withdrawal message executes against an already-drained pool has no recovery path: their accrued reward is silently and permanently lost, with no re-credit of `Fees<T>` and no retry mechanism. This is a fund-loss/lock outcome caused entirely by legitimate, permissionless activity (concurrent relayer withdrawals and timeout refunds), matching the external report's DoS-via-legitimate-contention pattern rather than requiring a malicious relayer, prover, or governance actor.

### Title
Relayer Fee Withdrawal Can Permanently Lose Accrued Rewards via Shared, Unreserved Fee-Token Pool - (File: modules/pallets/relayer/src/withdrawal.rs)

### Summary
`pallet-ismp-relayer` tracks each relayer's earned fee as a virtual balance (`Fees<T>`) backed by a single shared `feeToken()` balance on the source `EvmHost` contract. Withdrawal zeroes the virtual balance and dispatches a never-timing-out ISMP message to pay out from that shared pool, but nothing reserves or caps the sum of outstanding relayer entitlements against the pool's real balance, which is also concurrently drained by permissionless timeout refunds and other relayers' withdrawals.

### Finding Description
`EvmHost.feeToken()` balance is funded by all message dispatchers (`dispatch()` pulls `post.fee` into the host contract) and is drawn by (a) timeout refunds to senders, (b) governance protocol withdrawals, and (c) every relayer's fee withdrawal — all sharing one undifferentiated balance. `Fees<T>` on Hyperbridge is an accounting ledger of amounts "owed" from that pool, credited independently per relayer by `accumulate_fee_and_deposit_event`. `withdraw()` reads the accounted amount, zeros it, and dispatches a `WithdrawalParams`/`WithdrawParams` message with no timeout to the destination `HostManager`, which calls `EvmHost.withdraw()` → `IERC20.safeTransfer`. If the pool's real balance is below the requested amount at execution time (because other legitimate, unprivileged actors — other relayers withdrawing, or anyone submitting timeout proofs — already drained it), the transfer reverts and the relayer's already-zeroed claim is gone with no replay or re-credit path.

### Impact Explanation
Legitimate, unprivileged, concurrent use of the shared fee pool (multiple relayers withdrawing, or timeout refunds firing) can cause a relayer's provably-earned reward to be permanently and irrecoverably lost — a direct loss of funds for the rightful beneficiary, matching the "Impact Gate" category of loss of funds / logic attack, without requiring any malicious relayer, prover, or governance action.

### Likelihood Explanation
Requires only ordinary operational conditions: several relayers actively delivering and withdrawing fees against the same source chain, or routine message timeouts, both of which happen continuously in production. No privileged access, malicious peer, or front-running condition is needed.

### Recommendation
Track outstanding relayer liabilities against the `EvmHost` fee-token balance explicitly (e.g., a reserved/committed counter decremented only on confirmed payout), reconcile before zeroing `Fees<T>`, and/or make the withdrawal dispatch failure-aware (allow it to time out and re-credit `Fees<T>` on the source chain, or confirm destination balance sufficiency before optimistically clearing the ledger entry).

### Proof of Concept
1. Two relayers, R1 and R2, each accumulate real, legitimate fees of amount `X` against the same source-chain `EvmHost`, where the host's actual `feeToken()` balance is only `X` (not `2X`) due to concurrent timeout refunds having already consumed the rest.
2. R1 calls `withdraw_fees`; `Fees<T>[chain, R1]` is zeroed and a withdrawal `PostRequest` (timeout = 0) is dispatched — see `modules/pallets/relayer/src/withdrawal.rs:116-160`.
3. R1's message lands first and drains the remaining `X` from the pool via `EvmHost.withdraw()` (`evm/src/core/EvmHost.sol:651-660`).
4. R2 calls `withdraw_fees`; `Fees<T>[chain, R2]` is zeroed identically, and its `WithdrawParams` message is dispatched.
5. When R2's message executes, `IERC20(token).safeTransfer` reverts (insufficient balance) since the pool is now empty; R2's `Fees<T>` entry was already zeroed in step 4 and is never restored — R2 has permanently lost its earned reward, with no malicious actor involved anywhere in the flow.

### Citations

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

**File:** evm/src/core/EvmHost.sol (L885-905)
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
```

**File:** evm/src/core/EvmHost.sol (L921-948)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }

        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-368)
```rust
	pub fn accumulate_fee_and_deposit_event(
		state_machine: StateMachine,
		address: Vec<u8>,
		fee: U256,
	) {
		let _ = Fees::<T>::try_mutate(state_machine, address.clone(), |inner| {
			*inner += fee;
			Ok::<(), ()>(())
		});

		Self::deposit_event(Event::<T>::AccumulateFees {
			address: sp_runtime::BoundedVec::truncate_from(address),
			state_machine,
			amount: fee,
		});
	}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L16-30)
```rust
//! Relayer fee withdrawal.
//!
//! Once fees have been accumulated into [`crate::pallet::Fees`] by
//! [`crate::accumulate`], relayers withdraw them via [`Pallet::withdraw`].
//! The flow:
//!
//! 1. The relayer signs a `(nonce, dest_chain, beneficiary?)` payload with their per-chain key (EVM
//!    secp256k1 / sr25519 / ed25519).
//! 2. The pallet verifies the signature, increments the per-relayer nonce, and dispatches an ISMP
//!    POST request to the destination's host manager (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate)
//!    instructing it to disburse `available_amount` of the fee token to the beneficiary.
//! 3. The `Fees` entry is zeroed so the same balance cannot be withdrawn twice.
//!
//! The on-chain effect is just dispatching the message; the destination chain settles the
//! payout when the ISMP request is delivered there.
```

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
