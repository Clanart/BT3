### Title
Escrow credited from declared amounts instead of verified balance after unchecked `IERC20.transfer` inside `CallDispatcher.dispatch`, allowing escrow over-crediting on the Tron `IntentGatewayV2` predispatch path - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The external report's core defect — trusting an ERC20 `transfer`/`transferFrom` call's success without checking its boolean return value — has a direct local analog in the Tron fork of `IntentGatewayV2`. In the `placeOrder` predispatch branch, the sweep transfer from the `CallDispatcher` back to the gateway is executed as a raw low-level call inside `CallDispatcher.dispatch`, which only checks the call's `success` flag and discards the ABI-encoded boolean return data of `IERC20.transfer`. Unlike the mainline EVM `IntentGatewayV2.sol`, the Tron variant then credits escrow using the *declared* order amount rather than a post-transfer balance measurement, so a `transfer` that returns `false` without reverting is silently treated as a full, successful transfer.

### Finding Description
`CallDispatcher.dispatch` executes each queued `Call` with a raw low-level call and only reverts if the call itself reverts: [1](#0-0) 

It never inspects `result`, i.e. the ABI-encoded `bool` that `IERC20.transfer` returns. A call to `transfer` that returns `false` (e.g., due to insufficient balance handled non-reverting, a non-standard token, or a fee/blacklist condition) is indistinguishable from a real transfer as far as `CallDispatcher` is concerned.

In `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`, the predispatch branch builds exactly this kind of raw sweep call: [2](#0-1) 

Crucially, after this sweep call executes, escrow accounting is derived from `reducedInputs[i].amount` — the value computed from the *declared* `order.inputs[i].amount` minus protocol fee — not from a post-call `balanceOf(address(this))` measurement: [3](#0-2) 

Compare this to the mainline EVM `IntentGatewayV2.sol`, which performs the security-critical reconciliation this Tron variant is missing: it snapshots the gateway's balance before the sweep, executes the same kind of sweep call, and then re-measures the actual balance delta, overwriting `order.inputs[i].amount` with what was *actually received* before computing fees/commitment/escrow: [4](#0-3) 

The existence of this explicit balance-diff reconciliation in the mainline contract shows the author already recognized that a `Call`-dispatched `transfer` cannot be trusted to have actually moved the stated amount — yet the Tron fork of the same function omits it, crediting escrow with the pre-agreed amount regardless of what the token contract actually delivered.

### Impact Explanation
This falls under bridge custody / escrow accounting corruption: the on-chain escrow ledger `_orders[commitment][token]` can be inflated relative to the token balance the gateway actually holds. When the order is later filled and the destination chain triggers `RedeemEscrow`/withdrawal against this ledger, the gateway may attempt to pay out more of a given token than it actually received for that specific order — either reverting for that order (griefing/fund lock) or, in the pooled multi-order token balance, disbursing tokens that were escrowed by other users, causing loss of funds for those users. This is a false-state-acceptance-style bug: the contract's internal accounting silently accepts an unproven transfer outcome as ground truth.

### Likelihood Explanation
The predispatch flow is a normal, unprivileged, user-facing entrypoint (`placeOrder` with `order.predispatch.call`/`assets` populated) — no relayer, prover, or admin involvement is required. Any ERC20 that can return `false` on transfer without reverting (several non-standard/legacy tokens, or a token with balance/blacklist edge cases) triggers the divergence between `CallDispatcher`'s success check and actual token movement.

### Recommendation
Apply the same fix already present in the mainline EVM `IntentGatewayV2.sol`: after the sweep `dispatch` call, re-measure `IERC20(token).balanceOf(address(this))` against a pre-call snapshot and use that verified delta (not the declared `order.inputs[i].amount`/`reducedInputs[i].amount`) when crediting `_orders[commitment][token]`. Additionally, `CallDispatcher.dispatch` could decode and check the boolean return value for calls it knows are ERC20 transfers, or the caller should always use `SafeERC20`-style verification (checking `balanceOf` deltas, as OpenZeppelin's `safeTransfer`/`safeTransferFrom` effectively force) rather than relying on raw `.call` success.

### Proof of Concept
1. Attacker (order creator) constructs an ERC20-like token where `balanceOf` reports a normal balance but `transfer` returns `false` instead of reverting under some condition they control (e.g., a custom "declining" transfer or a transfer that partially succeeds).
2. Attacker calls `placeOrder` on the Tron `IntentGatewayV2` with `order.predispatch.call`/`assets` set and `order.inputs[i].token` = this token, requesting `amount = X`.
3. `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` executes the predispatch logic; the subsequent sweep `dispatch(abi.encode(transferCalls))` calls `token.transfer(address(this), balance)` from the dispatcher — this call returns `false` but does not revert, so `CallDispatcher.dispatch` treats it as successful (`evm/src/utils/CallDispatcher.sol:59-60`).
4. `_orders[commitment][token] += reducedInputs[i].amount` credits the full declared amount even though the gateway received zero or a reduced amount of the token (`evm/tron/contracts/apps/IntentGatewayV2.sol:430-440`).
5. Escrow ledger now overstates the gateway's actual token holdings for this order, corrupting downstream fill/redeem/refund accounting for that token.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-443)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L229-280)
```text
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
```
