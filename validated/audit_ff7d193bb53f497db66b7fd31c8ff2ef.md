## Title
Intent escrow ledger credited with nominal (fee/tax-unadjusted) amount instead of actually-received balance in Tron IntentGatewayV2 — `evm/tron/contracts/apps/IntentGatewayV2.sol`

### Summary
This is a direct analog of the reported `mint()`/`burn()` bug: an internal accounting variable (`totalSupply` in the report; here `_orders[commitment][token]`, the per-order escrow ledger) is updated using a value that does not correspond to the real token movement that actually occurred, so the ledger drifts from the contract's real custodied balance. In `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`, the escrow ledger is credited with `reducedInputs[i].amount` (the requested amount minus the protocol fee) instead of the amount actually received by the contract, unlike the canonical (already-fixed) EVM contract at `evm/src/apps/IntentGatewayV2.sol`, which explicitly measures balance deltas before crediting escrow.

### Finding Description
In the fixed/current mainline contract, `placeOrder` measures actual token deltas before trusting them: [1](#0-0) 

Both the predispatch and non-predispatch branches mutate `order.inputs[i].amount` to the **measured** balance delta (`balanceOf(after) - balanceOf(before)`) before it is used to compute the commitment and credit escrow, explicitly to defend against fee-on-transfer tokens: [2](#0-1) 

The Tron variant of this same contract does **not** carry this fix. In its non-predispatch branch it calls `safeTransferFrom` for `order.inputs[i].amount` but credits the ledger with `reducedInputs[i].amount` — a value derived purely from the user-supplied `order.inputs[i].amount` minus the protocol fee, with no measurement of what the contract actually received: [3](#0-2) 

The predispatch branch has the identical flaw — dust is only checked against `dispatcher`'s balance vs `requiredAmount`, but the escrow credit again uses `reducedInputs[i].amount`, not the amount actually swept back into `address(this)`: [4](#0-3) 

For any token with a transfer tax, deflationary/rebasing mechanics, or blacklist-partial-transfer behavior, the real balance credited to the contract is less than `order.inputs[i].amount`, yet `_orders[commitment][token]` is inflated to `order.inputs[i].amount - protocolFee`. Because `_orders` is a per-commitment ledger against a **single pooled ERC20/TRC20 balance held by the contract** (all orders using the same token share one `balanceOf(address(this))`), this desync is not contained to the order that caused it.

Later redemption (`withdraw`) pays out strictly from the ledger value, with no cross-check against the token's actual pooled balance: [5](#0-4) 

### Impact Explanation
Because the escrow ledger can be inflated beyond what was actually deposited for a given token, the shared token pool can be drawn down below what is needed to honor other, legitimate orders' escrow entries for that same token. This is a direct "loss of funds" / broken-invariant path: order A's real deposit can be consumed to satisfy order B's inflated ledger entry, leaving order A unable to be refunded/redeemed in full. It matches the report's core defect class exactly — an accounting variable used for downstream fund-moving calculations is not kept consistent with the real balance effect of mint/burn (here, escrow credit vs. actual transfer-in), and multiple calculations throughout the withdrawal/refund/cancel paths (`_withdraw`, dust-fee accounting, `_orders[commitment][TRANSACTION_FEES]`) all trust this ledger as ground truth.

### Likelihood Explanation
`placeOrder` is a fully public, unprivileged entry point, callable by anyone with no allow-list on the input token address — the attacker chooses the ERC20/TRC20 contract used as `order.inputs[i].token`. Any tax/fee-on-transfer or otherwise lossy token is sufficient to trigger the desync; no relayer, prover, governance, or privileged actor is required. The bug is a straightforward omission of a fix that the team already applied to the sibling EVM contract, confirming the class of token behavior is a recognized, real threat model for this codebase (see the explicit "Phase 1" comment and fee-on-transfer regression test in the main EVM suite: `evm/tests/foundry/IntentGatewayV2SameChainTest.sol` lines ~2461-2547).

### Recommendation
Port the "measure actual received balance" fix from `evm/src/apps/IntentGatewayV2.sol` (balance-before/after diffing in both the predispatch-sweep and direct-transfer branches) into `evm/tron/contracts/apps/IntentGatewayV2.sol`, and use the measured delta — not the nominal `order.inputs[i].amount`/`reducedInputs[i].amount` — both to compute the commitment hash and to credit `_orders[commitment][token]`.

### Proof of Concept
1. Attacker deploys a TRC20 token `EVIL` with a configurable transfer tax (e.g., 50%).
2. Attacker calls `placeOrder` on the Tron `IntentGatewayV2` with `order.inputs[0] = {token: EVIL, amount: 1000}`, no predispatch.
3. `safeTransferFrom(attacker, address(this), 1000)` moves only `500` real `EVIL` tokens into the contract due to the tax, but:
   `_orders[commitment][EVIL] += reducedInputs[0].amount` credits `~999` (1000 minus a small protocol fee), not `500`.
4. If any other order also escrows `EVIL` (or the attacker repeats this to drain a token that other legitimate users also use), a subsequent legitimate `withdraw`/refund call for that token can fail or be shorted, because the pooled `EVIL` balance held by the contract is insufficient to cover the sum of all ledger entries — the accounting-vs-custody mismatch (`_orders` totals > actual `balanceOf(address(this))`) is now provable on-chain, mirroring the reported `totalSupply` desync from `mint()`/`burn()`.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L198-280)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L282-298)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
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
