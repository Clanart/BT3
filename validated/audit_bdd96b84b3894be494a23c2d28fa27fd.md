### Title
Dead emergency-pause flag: `IntentGatewayV2` declares `_paused` but no function ever checks it, so `placeOrder`/`fillOrder`/`cancelOrder`/`withdraw` continue moving escrowed funds during an active exploit - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase.sol` declares a public `_paused` boolean state variable explicitly commented as "Appended last to preserve existing storage slots" [1](#0-0) , implying an intended emergency-pause mechanism for the IntentGateway. However, across the entire `evm/src/apps/**` tree there is no `whenNotPaused`/`whenPaused` modifier, no setter that writes to `_paused`, and no read of `_paused` anywhere in `IntentGatewayV2.sol`, `IntrinsicIntents.sol`, or `ExtrinsicIntents.sol`. This is the same broken invariant as the `BaseGauge` report: a pause switch exists in storage but is not wired into the fund-moving entry points (`placeOrder`, `fillOrder`, `cancelOrder`, `_withdraw`, `withdraw`).

### Finding Description
The Hyperbridge IntentGateway V2 escrows user funds in `placeOrder` [2](#0-1) , releases them to solvers in `fillOrder` (routing to `_fillSameChain` and the extrinsic cross-chain fill path) [3](#0-2) [4](#0-3) , and refunds them via `_withdraw`/`withdraw` on cancellation or timeout [5](#0-4) [6](#0-5) .

None of these functions carry a pause guard. Only `nonReentrant` protects them. The `_paused` variable sits unused in storage — it was seemingly reserved/appended for a future pause feature (per its comment) but the corresponding modifier and setter were never implemented, or were removed, leaving a vestigial but publicly readable flag that gives operators/users a false sense that an emergency stop exists.

This differs from the sibling apps in the same codebase: `HyperFungibleToken.sol`, `HyperFungibleTokenUpgradeable.sol`, `WrappedHyperFungibleToken.sol`, and `HyperbridgeLzEndpoint.sol` all correctly use OpenZeppelin's `Pausable` with `whenNotPaused` applied to `send`, `onAccept`, and `onPostRequestTimeout` [7](#0-6) . The IntentGateway, which custodies far larger cross-chain escrow value and has a much more complex attack surface (calldata execution, solver selection, partial fills, cross-chain settlement), has no equivalent enforcement despite declaring the storage slot for it.

### Impact Explanation
If Hyperbridge governance/operators ever detect an exploit in the IntentGateway (e.g., a bug in `select`/`fillOrder` solver-binding logic, a calldata-execution abuse, or a price-oracle manipulation affecting surplus distribution) there is no on-chain mechanism to halt `placeOrder`, `fillOrder`, or `cancelOrder`. An attacker who has found any other exploitable path into escrowed funds can continue draining `_orders` balances even after the incident is discovered, because there is no `whenNotPaused` check anywhere to stop it — unlike the token-bridging apps in the same repo which can be frozen instantly. This maps directly to the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" categories: the missing control doesn't itself create a new fund-diversion primitive, but it removes the only available circuit breaker for any other bug in escrow custody, extending the blast radius and duration of any future incident to the maximum possible.

### Likelihood Explanation
Likelihood of the underlying storage slot mattering is contingent on there being some other exploitable bug to pause — this finding on its own is a missing-safeguard/defense-in-depth gap rather than a directly exploitable primitive. It is a certain gap in the code today (confirmed by exhaustive `grep` across `evm/src/**` for `Pausable`, `whenNotPaused`, `_pause`, `paused`, `_paused` — the only hits are the unused declaration in `IntentsBase.sol`), and it applies to the contract holding the largest amount of cross-chain escrowed user/solver value in the repo.

### Recommendation
Either remove the dead `_paused` variable to avoid misleading auditors/operators, or properly wire it up: add an admin/governance-gated `setPaused(bool)` function (routed through the same `UpdateParams`/governance dispatch path already used for `_updateParams`) and apply a `whenNotPaused` (or equivalent `if (_paused) revert Paused();`) guard to `placeOrder`, `fillOrder`, `cancelOrder`, `select`, and the internal `_withdraw`/`withdraw` paths, consistent with the pattern already implemented in `HyperFungibleTokenUpgradeable.sol`.

### Proof of Concept
```solidity
// evm/src/apps/intentsv2/IntentsBase.sol
bool public _paused;  // declared but never read/written anywhere in the app

// evm/src/apps/IntentGatewayV2.sol
function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
    // no whenNotPaused / _paused check
    ...
}

function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
    // no whenNotPaused / _paused check
    ...
}
```
Grep across `evm/src/**/*.sol` for `Pausable|whenNotPaused|paused` returns matches only in `evm/src/apps/intentsv2/IntentsBase.sol` for the unused `_paused` declaration — no setter, no modifier, no call site exists in the IntentGateway contracts, confirming the pause mechanism is entirely non-functional while user/solver funds continue to flow through `placeOrder`/`fillOrder`/`withdraw` unconditionally.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L160-161)
```text
    /// @dev Appended last to preserve existing storage slots.
    bool public _paused;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-413)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-54)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-349)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }

    /**
     * @notice Handles timeout of a previously dispatched cross-chain transfer
     * @dev Called by the ISMP host when a sent message times out without being delivered.
     * Re-mints the burned tokens back to the original sender as a refund.
     * @param incoming The timed-out POST request and the relayer that submitted the timeout proof
     */
    function onPostRequestTimeout(PostRequestTimeout memory incoming) external override onlyHost whenNotPaused {
        Message memory message = abi.decode(incoming.request.body, (Message));
        address refundee = _toAddr(message.from);
        _mint(refundee, message.amount);
        emit Refunded({to: refundee, amount: message.amount});
    }
```
