### Title
Escrow Credited From Declared Amount Instead of Actual Swept Balance in Tron IntentGatewayV2.placeOrder Predispatch Path - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The external report's core defect is: a function credits a user based on a value read/assumed *after* a balance-affecting operation instead of tracking the real, verified amount that was actually moved, so the ledger and reality diverge and funds get lost or misallocated. The Tron variant of `IntentGatewayV2.placeOrder` reproduces this exact defect in its predispatch escrow path: it credits `_orders[commitment][token]` using the pre-computed, caller-declared `reducedInputs[i].amount` rather than the amount actually verified to be received by the gateway, and it never checks the ERC20 boolean return value of the sweep transfer, nor rejects duplicate input tokens.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `placeOrder`'s predispatch branch builds sweep calls that pull funds from the `CallDispatcher` back to the gateway: [1](#0-0) 

For each input `i`, the code:
1. Reads `balance` (native `address(dispatcher).balance` or `IERC20(token).balanceOf(dispatcher)`) — read once, before any sweep has executed.
2. Builds `transferCalls[i]` to move that `balance` to `address(this)`, using a raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` whose only success check is the low-level call success flag — the actual ERC20 `bool` return value is never inspected.
3. Immediately credits the ledger with `_orders[commitment][token] += reducedInputs[i].amount;` — **before** the sweep (`ICallDispatcher(dispatcher).dispatch(...)`) is even invoked at line 443, and using the *declared/reduced* amount rather than any measured "before/after" delta.

Compare this with the sibling contract `evm/src/apps/IntentGatewayV2.sol`, which fixes exactly this class of bug by snapshotting balances *before* the sweep and computing `received = address(this).balance - balancesBefore[i]` (or the ERC20 equivalent) *after* the sweep executes, then crediting escrow only with the verified `received` amount: [2](#0-1) 

That version additionally guards against duplicate input tokens at credit time with an explicit revert: [3](#0-2) 

The Tron variant has neither protection: it uses `_orders[commitment][token] += ...` (accumulating, not `=` with a duplicate-token revert) and never reconciles credited amounts against a verified pre/post balance delta: [4](#0-3) 

This is the same broken invariant as the WETH report: the contract assumes a balance-affecting transfer succeeded for the full expected amount and credits a beneficiary ledger accordingly, without confirming the actual balance change. Here, for any ERC20 token whose `transfer()` can return `false` without reverting (a well-known, common non-standard ERC20 behavior — precisely the reason `SafeERC20` exists, which the sibling contract uses via `safeTransferFrom`/`safeTransfer` but this Tron contract bypasses via raw `.call`), the low-level call still reports `success = true` even though zero tokens moved. The gateway would then credit `_orders[commitment][token]` with `reducedInputs[i].amount` while custody of that amount was never actually established.

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant. An escrow ledger entry (`_orders[commitment][token]`) can be created/inflated without matching real token custody in the gateway. When the order is later cancelled (`onGetResponse`/`withdraw`) or filled, the beneficiary/withdraw path pays out from `_orders[commitment][token]` regardless of whether the gateway actually holds the backing tokens — this can drain the gateway's real balance backing *other* users' legitimately escrowed funds, i.e. fund loss for other order owners, or unauthorized payout beyond what any single user actually deposited.

### Likelihood Explanation
Triggering this requires the escrowed input token to be a non-reverting ERC20 whose `transfer` can return `false` on failure (a real subset of deployed ERC20 tokens) routed through the predispatch/CallDispatcher sweep path, which is an unprivileged, user-supplied order feature (`order.predispatch`). No relayer, prover, or admin is needed — `placeOrder` is a fully public entrypoint and the attacker fully controls `order.inputs`, `order.predispatch`, and the token selection.

### Recommendation
Mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`: snapshot `address(this).balance` / `IERC20(token).balanceOf(address(this))` immediately before executing the sweep `dispatch` call, execute the sweep, then compute `received = after - before` and credit escrow (`_orders[commitment][token]`) only with the verified `received` amount (capped/reconciled against `reducedInputs[i].amount`), never with the pre-transfer declared amount. Additionally, replace raw `token.call(...)` sweep encodings with `SafeERC20.safeTransfer`/`safeTransferFrom` so failed ERC20 transfers (including those that return `false` instead of reverting) cause the whole predispatch batch to revert, and add the same duplicate-input-token guard (`revert` on `_orders[commitment][token] != 0` before assignment) that the sibling `IntentsBase.sol`/main EVM contract already enforces.

### Proof of Concept
Conceptual PoC (cannot be executed without the Tron test harness, but the exploit chain is directly readable from the code):
1. Deploy a test ERC20 whose `transfer()` returns `false` (does not revert) once its balance is insufficient — a legal ERC20 per spec, and exactly the class of token `SafeERC20` exists to guard against.
2. Attacker calls `placeOrder` with `order.predispatch` configured so the `CallDispatcher` ends up holding less of this token than `order.inputs[i].amount` claims (e.g., via a predispatch call that only partially funds the dispatcher, or via the `balance < requiredAmount` check being satisfied exactly at the edge while a duplicate token entry consumes the same funds twice).
3. In the sweep loop (`evm/tron/contracts/apps/IntentGatewayV2.sol:410-440`), the second sweep call for the same token returns `false` from `transfer()` (call succeeds at the low level) while `_orders[commitment][token] += reducedInputs[i].amount` still executes for that entry.
4. `_orders[commitment][token]` now reflects more tokens than the gateway actually custodies.
5. Attacker cancels the order (`onGetResponse` → `withdraw`) and receives a refund funded by other users' escrowed balance of the same token, since `_orders` is a shared per-token ledger not backed 1:1 by verified transfers. [5](#0-4)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-721)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
    }

    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
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

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```
