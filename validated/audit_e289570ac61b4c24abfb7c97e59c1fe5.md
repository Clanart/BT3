### Title
Permanent fund lock in Intent Gateway cross-chain settlement when beneficiary is blacklisted by a transfer-restricted token (e.g. USDC) - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase._withdraw()` — the single function that releases escrowed cross-chain intent collateral to a solver or refunds it to a user — pushes tokens directly to the `beneficiary` address via `IERC20.safeTransfer`. This function is invoked from `ExtrinsicIntents.onAccept()` (for `RedeemEscrow`/`RefundEscrow` messages) and `ExtrinsicIntents.onGetResponse()` (for source-chain cancellation). If the `beneficiary` (the solver on fill, or `order.user` on refund/cancel) is blacklisted by a transfer-restricted token such as USDC, the `safeTransfer` call reverts, causing the entire ISMP callback to revert — permanently, since the underlying condition (blacklist status) does not change. Because `_filled[commitment]` is only marked *inside* the same reverting call, and the paired action on the counterpart chain has already been finalized irreversibly (order marked filled/cancelled), the escrowed collateral becomes permanently stuck with no pull-based recovery path.

### Finding Description
`_withdraw()` in `evm/src/apps/intentsv2/IntentsBase.sol:390-425` iterates over the withdrawal request's tokens and does:
```solidity
IERC20(token).safeTransfer(beneficiary, amount);
```
`beneficiary` is attacker/user controlled data carried in the `WithdrawalRequest` (decoded from the cross-chain message body or GET-response context), and can be the order's `user` or the filling `solver`.

This function is called from three one-shot execution paths:
- `ExtrinsicIntents.onAccept()` (`RequestKind.RedeemEscrow` / `RequestKind.RefundEscrow`) — `evm/src/apps/intentsv2/ExtrinsicIntents.sol:289-295`
- `ExtrinsicIntents.onGetResponse()` (source-chain cancellation confirmation) — `evm/src/apps/intentsv2/ExtrinsicIntents.sol:319-324`
- `IntrinsicIntents._cancelSameChain()` for the same-chain case — `evm/src/apps/intentsv2/IntrinsicIntents.sol:161-187`

Each of these is triggered by delivery of a one-time cross-chain message/receipt (bound by `commitment`), and each is `onlyHost`-gated so it can only be executed once per commitment through the normal flow. Critically, the *counterpart* side of the flow is already irreversibly finalized before this transfer happens:
- In `_cancelFromDest()` (`ExtrinsicIntents.sol:240-267`), the destination chain sets `_filled[commitment] = order.user` and dispatches `RefundEscrow` to the source chain **before** the source-side `_withdraw` ever executes. If `order.user` is blacklisted by the escrowed token (e.g., USDC) on the source chain, `onAccept()` on the source chain will always revert on `safeTransfer`, since USDC blacklist status doesn't change through relayer retries — no relayer or state-proof condition changes the outcome.
- Likewise, `_cancelFromSource()` dispatches a `DispatchGet` and the response handler `onGetResponse()` unconditionally calls `_withdraw(body, true, true)` with `beneficiary = order.user`, which will permanently revert under the same blacklist condition.
- On the fill/redeem path, `_fillCrossChain()` on the destination marks `_filled[commitment] = msg.sender` and dispatches `RedeemEscrow` back to source with `beneficiary = msg.sender` (the solver); if that solver address is blacklisted, the corresponding source-side release permanently reverts, locking the solver's payout.

There is no pull/claim fallback: the tokens sit in the gateway contract's escrow (`_orders[commitment][token]`), but no external function allows the beneficiary (or anyone else) to withdraw to an alternate address once the automatic push path is permanently blocked, matching the exact broken invariant identified in the source report (push instead of pull, with a 0-argument-independent unconditional transfer that can never succeed for a blacklisted recipient).

### Impact Explanation
This is a direct, permanent loss/lock of bridged escrow funds (an unprivileged, no-malicious-actor-required condition: the ordinary and expected event of a wallet address being placed on a USDC/USDT blacklist). Since the destination-side state (`_filled[commitment]`) is already finalized irrevocably before the source-side transfer is attempted, and the source-side transfer can never succeed once blocked, the escrowed collateral for that order is permanently unrecoverable through the protocol's normal one-shot settlement paths. This directly matches the bounty's "stealing or loss of funds" impact category for bridged asset custody (order escrow / refunds), since funds move exactly once by design but here can be made to never move at all while remaining locked in the gateway.

### Likelihood Explanation
Likelihood is low-to-moderate but entirely realistic: it only requires that a legitimate user's or solver's address related to escrow settlement becomes blacklisted by the underlying stablecoin issuer (a known, real-world occurrence for USDC/USDT) at any point between order placement and settlement/cancellation. No relayer, prover, or governance compromise is needed — this is a standard token-compliance event colliding with an unconditional push-based transfer in the settlement critical path.

### Recommendation
Adopt a pull-over-push pattern for `_withdraw()`: on transfer failure (e.g., via try/catch around `safeTransfer` or a low-level call check), credit the amount to an internal claimable balance keyed by `(beneficiary, token)` instead of reverting the whole settlement callback. Provide a separate `claim()` function that lets the beneficiary (or an address they designate) pull the tokens, and mark `_filled[commitment]` and any related state changes as final regardless of whether the immediate push succeeds, so a single stuck transfer cannot revert the entire cross-chain settlement/cancellation flow.

### Proof of Concept
1. User places a cross-chain intent order on chain A, escrowing USDC as `order.inputs`, with `order.user = U`.
2. Before the order deadline, `U`'s address is added to USDC's blacklist (e.g., due to unrelated compliance action).
3. The order is never filled; after the deadline, anyone calls `cancelOrder()` on the destination chain, which invokes `_cancelFromDest()` (`ExtrinsicIntents.sol:240-267`). This sets `_filled[commitment] = U` on the destination and dispatches a `RefundEscrow` `DispatchPost` back to chain A with `beneficiary = U`.
4. A relayer delivers the message to chain A; `onAccept()` decodes the `WithdrawalRequest` and calls `_withdraw(body, true, true)` (`IntentsBase.sol:390-425`), which attempts `IERC20(USDC).safeTransfer(U, amount)`.
5. Because `U` is blacklisted, `safeTransfer` reverts (USDC's `transfer` reverts for blacklisted recipients), reverting the entire `onAccept()` call.
6. Every subsequent relayer retry hits the same revert since `U`'s blacklist status is unchanged. The destination chain already marked the order as cancelled/finalized (step 3), so there is no alternate path to re-trigger settlement with a different beneficiary — the escrowed USDC on chain A is permanently stuck in the gateway contract with no claim mechanism. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-324)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }

    /**
     * @dev Handles the response to a Hyperbridge GET request dispatched during
     * `_cancelFromSource`. Verifies that the `_filled` storage slot on the destination
     * chain is empty (meaning the order was never filled), then refunds the escrowed
     * tokens to the original user. Reverts with `Filled` if the slot is non-empty.
     *
     * @param incoming The incoming GET response from Hyperbridge containing the storage proof.
     */
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
