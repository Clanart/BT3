Found a genuine local analog in `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `placeOrder`, in the predispatch escrow-accounting branch.

### Title
`placeOrder()` escrows fee-reduced amounts computed before external predispatch call, allowing solver-controlled dust to corrupt escrow accounting - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`placeOrder`'s predispatch branch computes `reducedInputs[i].amount` (protocol-fee-reduced amounts) up front, from `order.inputs[i].amount` as originally declared by the user, *before* the `CallDispatcher` executes arbitrary predispatch calldata. The subsequent escrow write, `_orders[commitment][token] += reducedInputs[i].amount`, uses this pre-computed value rather than re-deriving the fee-adjusted amount from the balance actually swept back from the dispatcher. This mirrors the reported bug class exactly: a locally cached, pre-mutation accounting value is written into a running total after a state-changing sub-operation (here, the predispatch call and dispatcher balance sweep) has already taken effect, instead of being re-derived from the authoritative post-mutation source of truth (the actual balance transferred into escrow).

### Finding Description
In the predispatch branch of `placeOrder` (evm/tron/contracts/apps/IntentGatewayV2.sol:381-440):

1. `reducedInputs[i].amount = originalAmount - protocolFee` is computed against `order.inputs[i].amount`, the amount the user *declared*, before any tokens move (lines 353-368).
2. The predispatch call dispatcher then executes attacker/solver-influenced calldata (`order.predispatch.call`) which can produce a different actual balance than declared (line 408).
3. The code measures `balance` on the dispatcher and reverts if it's below `requiredAmount` (`order.inputs[i].amount`, the un-reduced value) — but the amount stored to escrow is the earlier `reducedInputs[i].amount`, not a value derived from the actually swept `balance` (line 435: `_orders[commitment][token] += reducedInputs[i].amount;`).
4. Contrast with the non-predispatch branch and with `IntentGatewayV2.sol` in `evm/src/apps/` (the non-tron variant), where `order.inputs[i].amount` is corrected to the *actual* transferred amount (`received = balanceAfter - balanceBefore`, line ~267 in `evm/src/apps/IntentGatewayV2.sol`) before fees/escrow are computed — closing exactly this gap. The `evm/tron/contracts/apps/IntentGatewayV2.sol` variant instead escrows `reducedInputs[i].amount`, a value derived purely from the pre-dispatch declared amount, never reconciled against the dust/excess actually swept in from the dispatcher.

Because `balance` (from the dispatcher, post-predispatch-call) can legitimately exceed `requiredAmount` (dust is only emitted as an event, not reconciled into escrow), and because the escrow increment uses the untouched `reducedInputs[i].amount` computed against the user's original declaration rather than the post-call `balance`, any discrepancy between declared and actually-received amounts (fee-on-transfer tokens, predispatch call side effects, rounding) is silently absorbed as "dust" instead of being reflected in `_orders[commitment][token]`. This breaks the invariant that on-chain escrow accounting for a commitment always matches the tokens actually custodied for it — the same broken invariant class as the report's stale `totalLiquidity` being written back without reflecting the true post-mutation state.

### Impact Explanation
Escrow accounting (`_orders[commitment][token]`) is the value later paid out to solvers on `fillOrder`/`_withdraw` or refunded on cancellation. If the recorded escrow amount can diverge from the reality of the amount actually available/custodied — as a direct consequence of writing a pre-call cached value instead of a post-call reconciled one — downstream withdrawals/fills can attempt to pay out more than is backed, or the protocol perpetually strands "dust" that should have belonged to escrow accounting. This falls under the bounty's fund-loss / logic-attack category since it corrupts order-escrow bookkeeping used for beneficiary payouts.

### Likelihood Explanation
The predispatch path is reachable by any unprivileged order placer (`placeOrder` is public, no privileged role required) who controls the `order.predispatch.call` payload and `order.predispatch.assets`. No malicious relayer, prover, or governance actor is needed — the discrepancy is self-inflicted purely by the interaction between predispatch calldata effects and fixed-point fee computation done before the call. However, the actual attacker-exploitable magnitude depends on being able to reliably create a balance/`reducedInputs` mismatch that benefits the attacker (rather than just leaking dust to the protocol), which requires further confirmation against the exact fee/commitment flow; this is flagged as the main residual uncertainty.

### Recommendation
Mirror the pattern already used in `evm/src/apps/IntentGatewayV2.sol`'s non-predispatch and predispatch branches: after the dispatcher sweep, recompute `order.inputs[i].amount` from the actual balance delta, then derive `reducedInputs[i].amount` (and thus the escrow increment) from that reconciled value — never from a value computed before the external predispatch call executes.

### Proof of Concept
Not independently executed; the analysis is based on static comparison between `evm/tron/contracts/apps/IntentGatewayV2.sol:331-463` (which escrows the pre-call `reducedInputs[i].amount`) and the corrected pattern in `evm/src/apps/IntentGatewayV2.sol:198-298` (which recomputes `order.inputs[i].amount` from the post-call balance delta before computing fees/escrow). A concrete PoC would need to construct a predispatch call whose swept-back balance differs from `order.inputs[i].amount` in a way that produces exploitable escrow drift, and confirm this against the full protocol-fee/commitment code path — this exact numeric exploitability was not verified end-to-end given tool constraints. [1](#0-0) [2](#0-1)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L331-463)
```text
     */
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }

        // escrow tokens
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L198-298)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));

            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```
