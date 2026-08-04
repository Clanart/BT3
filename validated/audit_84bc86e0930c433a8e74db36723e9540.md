I have enough evidence to confirm the analog. The `send()` function burns tokens (or locks underlying ERC20/native for the wrapped variant) on the source chain, and the corresponding refund path — `onPostRequestTimeout` — is the *only* mechanism that returns those funds to the user if the cross-chain message never gets delivered. That refund path is gated by `whenNotPaused`, exactly mirroring the `sDaiStrategy` bug: pausing blocks the emergency exit for funds already committed to the bridge.This confirms the vulnerable pattern across all four cross-chain token contracts (`HyperFungibleToken`, `HyperFungibleTokenUpgradeable`, `WrappedHyperFungibleToken`, `WrappedHyperFungibleTokenUpgradeable`): `send()` custodies user funds (burn or lock) immediately, and the *only* refund mechanism, `onPostRequestTimeout`, is gated behind `whenNotPaused`, so an owner-initiated pause (or an emergency pause during an active incident) permanently traps already-committed user funds with no recovery path.

### Title
Paused `HyperFungibleToken`/`WrappedHyperFungibleToken` blocks timeout refunds, permanently locking already-burned/locked user funds - (File: `sdk/packages/core/contracts/apps/HyperFungibleToken.sol`, `WrappedHyperFungibleToken.sol`, and their Upgradeable variants)

### Summary
`send()` in `HyperFungibleToken` and `WrappedHyperFungibleToken` custodies the user's funds up front — burning the ERC20 supply in the non-wrapped variant, or locking the underlying ERC20/native token in the wrapped variant — before dispatching a cross-chain POST request. If that request is never delivered and instead times out, the sole recovery mechanism is `onPostRequestTimeout`, which re-mints/refunds the committed funds back to the original sender. However, this refund function is guarded by `whenNotPaused` [1](#0-0) , and equivalently in the wrapped contract [2](#0-1) . If the owner pauses the contract (e.g., in response to a detected exploit, as an emergency circuit breaker) at any point before a timed-out message's refund is processed, every user with an in-flight `send()` whose message subsequently times out is unable to recover their burned/locked funds, exactly mirroring the `sDaiStrategy` bug class where pausing blocks legitimate withdrawal/exit paths for already-committed value.

### Finding Description
`send()` is only guarded by `whenNotPaused` at entry [3](#0-2) , meaning funds can be burned/locked freely while unpaused. Once dispatched, the request lives in the ISMP host until it's either delivered (`onAccept`) or times out (`onPostRequestTimeout`). Both of these terminal handlers are also `whenNotPaused`-gated [4](#0-3) [5](#0-4) . The `onPostRequestTimeout` call is triggered by the ISMP host once a timeout proof is submitted — it is not something the affected user can defer or retry once they've committed funds; if the contract happens to be paused at the moment the host calls this handler, the entire call reverts, and there is no alternate path to reclaim the burned/locked funds. The wrapped variant is strictly worse because it directly locks real user-supplied ERC20/native value (not just an internal ERC20 supply accounting entry), so a stuck refund represents concrete lost custody of third-party assets [6](#0-5) . This same pattern is duplicated in the upgradeable variants `HyperFungibleTokenUpgradeable.onPostRequestTimeout` and `WrappedHyperFungibleTokenUpgradeable.onPostRequestTimeout` [7](#0-6) [8](#0-7) . Nothing in the pause path checks whether there are pending timed-out requests before allowing `pause()` to be called [9](#0-8) , so the owner (or an attacker who compromises/social-engineers a pause trigger, or simply the protocol responding to an unrelated incident) can inadvertently or as a side effect freeze all pending refunds indefinitely, until `unpause()` is called — and there is no guarantee unpause happens promptly, especially if the pause was itself triggered by an ongoing security incident.

### Impact Explanation
Impact is Medium-to-High: user funds that are already burned (non-wrapped) or already locked as real ERC20/native tokens (wrapped) become unrecoverable for the duration of the pause. In the wrapped-token case this is a direct, though transient, fund-lock of externally-owned assets — precisely the "loss of funds" / "fund lock" category called out in the bounty scope. Because pausing is most likely to occur exactly when something has gone wrong (the scenario in which users most urgently need their refund), the pause and the need-for-refund events are correlated, making the failure mode more likely to manifest exactly when it's most harmful.

### Likelihood Explanation
Likelihood is Medium: it requires (a) an in-flight `send()` whose message times out, and (b) the contract being paused at the time the timeout proof is relayed and `onPostRequestTimeout` is invoked. Pauses are expected to be transient emergency actions, but timeouts can also cluster during exactly the kind of instability (relayer outages, chain congestion, or the same incident prompting the pause) that would also cause a pause to be active, increasing the real-world overlap probability beyond a naive independent-event estimate.

### Recommendation
Remove the `whenNotPaused` modifier from `onPostRequestTimeout` (and, if desired, keep `onAccept` paused-gated since that path mints/delivers new funds rather than returning already-committed ones). Refund/timeout paths that return previously-committed user funds should always remain callable regardless of pause state, consistent with the general principle that a pause should stop new custody-taking actions, not block users from exiting positions they already entered.

### Proof of Concept
1. Owner calls `pause()` on `WrappedHyperFungibleToken` (or it becomes paused due to an emergency response).
2. Prior to the pause, a user called `send()`, locking `amount` of the underlying ERC20 into the contract, and the corresponding ISMP POST request never gets delivered on the destination chain.
3. A relayer submits a timeout proof to the ISMP host, which calls `onPostRequestTimeout(incoming)` on the `WrappedHyperFungibleToken` contract.
4. Because the contract is currently paused, the `whenNotPaused` modifier on `onPostRequestTimeout` reverts the entire call [10](#0-9) .
5. The user's locked ERC20/native tokens remain stuck in the contract with no way to reclaim them until the contract is unpaused — and if the timeout proof submission is not retried/re-triggered automatically once unpaused, the funds may remain locked indefinitely depending on relayer behavior around resubmission.

### Citations

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L213-215)
```text
    function pause() external onlyOwner {
        _pause();
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L264-266)
```text
    function send(SendParams calldata params) external payable whenNotPaused {
        _burn(msg.sender, params.amount);
        DispatchPost memory request = _buildDispatchPost(params);
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-291)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
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

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L266-273)
```text
    function send(HyperFungibleToken.SendParams calldata params) external payable whenNotPaused {
        uint256 msgValue = msg.value;
        if (_isWeth && msgValue >= params.amount) {
            msgValue = msgValue - params.amount;
            IWETH(_underlying).deposit{value: params.amount}();
        } else {
            IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
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

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L344-349)
```text
    function onPostRequestTimeout(PostRequestTimeout memory incoming) external override onlyHost whenNotPaused {
        Message memory message = abi.decode(incoming.request.body, (Message));
        address refundee = _toAddr(message.from);
        _mint(refundee, message.amount);
        emit Refunded({to: refundee, amount: message.amount});
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleTokenUpgradeable.sol (L368-390)
```text
    function onPostRequestTimeout(PostRequestTimeout calldata incoming) external override onlyHost whenNotPaused {
        HyperFungibleTokenUpgradeable.Message memory message =
            abi.decode(incoming.request.body, (HyperFungibleTokenUpgradeable.Message));
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
