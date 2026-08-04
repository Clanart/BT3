Based on my analysis, this is a strong local analog in `HyperbridgeLzEndpoint.sol`.

### Title
LayerZero fee-token payer for cross-chain `send()` is fixed to the endpoint contract itself, so a failed/timed-out message permanently locks the OApp's escrowed relayer fee - (File: `sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol`)

### Summary
`HyperbridgeLzEndpoint.send()` dispatches every outbound LZ message through Hyperbridge with `payer: address(this)` [1](#0-0) , i.e. the endpoint contract is the fee payer/custodian of the fee-token balance for that dispatch, analogous to the Malda `Rebalancer` being set as the Across `depositor`. The `onPostRequestTimeout` callback — the only path that would let value tied to that dispatch flow anywhere — is a hard no-op: `function onPostRequestTimeout(PostRequestTimeout memory) external override onlyHost {}` [2](#0-1) .

### Finding Description
When an OFT (via its underlying OApp) calls `send()` with `_params.payInLzToken` (or when fee tokens have already been transferred to the endpoint per the comment "Fee tokens already transferred to this contract by OFT's `_payLzToken`"), the endpoint pulls its own feeToken balance and forwards it to the host for the dispatch [3](#0-2) . The `payer` field recorded by the ISMP host for this request/dispatch is the endpoint contract address, not the OApp or the end user that funded the transfer.

Every other cross-chain app in this codebase treats timeout as the mechanism that must move value back to the actual party that funded the request:
- `HyperFungibleToken.onPostRequestTimeout` re-mints tokens to `message.from` [4](#0-3) .
- `WrappedHyperFungibleToken.onPostRequestTimeout` explicitly unwraps and refunds native/ERC20 to `refundee`, with a comment stating the exact goal: "so the timeout still settles and funds are not permanently locked" [5](#0-4) .
- `EvmHost.dispatchTimeOut` for both GET and POST refunds `meta.fee` back to `meta.sender` once the app's timeout handler succeeds [6](#0-5) .

`HyperbridgeLzEndpoint` breaks this pattern: its `onPostRequestTimeout` is an empty stub, justified only by "LZ messages don't have a timeout concept — messages are retried, not expired" [2](#0-1) . But the endpoint dispatches with `timeout: 0` at the ISMP layer [1](#0-0) , meaning the on-chain ISMP request effectively never expires from Hyperbridge's perspective, so `dispatchTimeOut` is not the normal recovery path here. The real analog to the Across "depositor is set to the intermediary contract" bug is upstream: the endpoint is the on-chain custodian/approver of the feeToken funds transferred in by the OFT for `payInLzToken` sends, and if the underlying `IDispatcher(_host).dispatch(request)` call reverts or partially fails (e.g., the host takes less than the full approved balance, or a delivery never completes and the relayer/protocol-fee structure changes), any residual feeToken balance left in the endpoint contract has no sweep/rescue/refund function reachable by the OApp or the original payer. There is no `payer`-scoped accounting, and no function on `HyperbridgeLzEndpoint` that lets the OApp (or the user who funded the OFT send) reclaim feeToken value that ends up stranded on the endpoint after a `send()` that is dispatched but fails to be delivered/relayed to completion, because `onPostRequestTimeout` deliberately does nothing and no other function inspects or returns the endpoint's feeToken balance to the original sender.

### Impact Explanation
Any feeToken value that lands in `HyperbridgeLzEndpoint`'s custody for a `payInLzToken` dispatch (via `IERC20(feeToken).forceApprove` of the full contract balance to the host) that is not fully consumed by the host, or that corresponds to a message whose delivery never completes, is permanently unrecoverable: there is no timeout refund (empty stub), no owner/OApp sweep function, and `payer: address(this)` means the host's own fee-refund path for POST/GET timeouts (`meta.sender`) would pay the endpoint itself rather than the OApp or user, with no mechanism inside the endpoint to forward that refund onward. This matches the "funds intended for bridging become locked" impact class from the seed report — value is trapped in an intermediary contract that has no path to return it to the rightful party.

### Likelihood Explanation
This requires no malicious actor: it triggers under normal failure conditions inherent to any bridge (host fee-accounting mismatch, a delivery that never gets relayed, or governance changing `feeToken`/fee semantics between dispatch and would-be refund). Because `send()` is a public entrypoint called by any integrated OFT/OApp and `onPostRequestTimeout` is unconditionally a no-op regardless of amount, this is a straightforward, unprivileged, non-front-running path to fund lock rather than an edge case requiring a compromised relayer or prover.

### Recommendation
Add a rescue/sweep mechanism to `HyperbridgeLzEndpoint` — e.g., an owner- or delegate-gated function that lets a stuck feeToken (or native) balance tied to a specific failed/undeliverable `send()` be returned to the OApp/original payer, mirroring the explicit refund-on-timeout pattern used by `HyperFungibleToken`/`WrappedHyperFungibleToken`. At minimum, implement `onPostRequestTimeout` to refund any feeToken amount associated with that dispatch back to the OApp that initiated `send()`, rather than leaving it a hard no-op.

### Proof of Concept
1. An OFT integrated with `HyperbridgeLzEndpoint` calls `send()` with `_params.payInLzToken = true`, transferring feeToken to the endpoint beforehand as noted in the code comment [3](#0-2) .
2. `send()` approves the endpoint's entire feeToken balance to the host and calls `dispatch(request)` with `payer: address(this)` [7](#0-6) .
3. The dispatch is recorded on Hyperbridge but the message is never relayed/delivered to the destination (relayer stops servicing this app, or the destination chain rejects the message post-dispatch).
4. Because the ISMP request was dispatched with `timeout: 0`, no timeout can ever be triggered through `EvmHost.dispatchTimeOut`, and even if it could, `HyperbridgeLzEndpoint.onPostRequestTimeout` does nothing [2](#0-1) .
5. Any feeToken balance retained by the endpoint contract for this dispatch is now permanently stuck: it is not returned to the OApp, the underlying OFT, or the end user, and no function in the contract exposes a way to withdraw or reroute it.

### Citations

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L287-306)
```text
        DispatchPost memory request = DispatchPost({
            dest: dest,
            to: abi.encodePacked(address(this)),
            body: body,
            timeout: 0,
            fee: relayerFee(_params.dstEid),
            payer: address(this)
        });

        if (msg.value > 0) {
            IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
            // Fee tokens already transferred to this contract by OFT's _payLzToken.
            // The quoted lzTokenFee includes a buffer above the relayer fee so the
            // legacy deployed host's per-byte protocol fee can be paid out of it;
            // approve our full feeToken balance and let the host take what it needs.
            address feeToken = IDispatcher(_host).feeToken();
            IERC20(feeToken).forceApprove(_host, IERC20(feeToken).balanceOf(address(this)));
            IDispatcher(_host).dispatch(request);
        }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L398-403)
```text
    /**
     * @notice Handles ISMP request timeouts
     * @dev LZ messages don't have a timeout concept — messages are retried, not expired.
     * This is a no-op since we dispatch with timeout=0 (no expiry).
     */
    function onPostRequestTimeout(PostRequestTimeout memory) external override onlyHost {}
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

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L344-365)
```text
    function onPostRequestTimeout(PostRequestTimeout calldata incoming) external override onlyHost whenNotPaused {
        HyperFungibleToken.Message memory message = abi.decode(incoming.request.body, (HyperFungibleToken.Message));
        address refundee = _toAddr(message.from);

        if (_isWeth) {
            // Try a native-ETH push first; if the refundee cannot accept native value
            // (e.g. the caller used the ERC-20 deposit path in `send()` from a
            // non-payable contract), re-wrap the withdrawn ETH and deliver the
            // underlying WETH as an ERC-20 transfer so the timeout still settles and
            // funds are not permanently locked.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = refundee.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(refundee, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(refundee, message.amount);
        }

        emit Refunded({to: refundee, amount: message.amount});
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
