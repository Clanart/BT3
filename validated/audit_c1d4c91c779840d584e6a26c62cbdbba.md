Based on my investigation, the strongest local analog to the PearVault "in-flight transfer not reflected in valuation" bug class is a **concrete, file-verifiable control-flow gap** in the Tron port of `IntentGatewayV2`, not a hypothetical accounting window.

### Title
Missing Reentrancy Guard on Tron `IntentGatewayV2.placeOrder`/`cancelOrder` Allows Escrow-State Corruption via Attacker-Controlled Predispatch Calls — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The canonical EVM `IntentGatewayV2` protects every state-mutating entrypoint (`placeOrder`, `fillOrder`, `cancelOrder`, etc.) with OpenZeppelin's `nonReentrant` modifier [1](#0-0) . The Tron variant of the same contract, which mirrors the identical escrow/predispatch/dust logic, has **zero** occurrences of `nonReentrant`/`ReentrancyGuard` anywhere in the file [2](#0-1) , while still performing the same external-call-before-state-finalization pattern that guard exists to protect.

### Finding Description
`placeOrder` on Tron executes attacker-supplied `order.predispatch.call` through `ICallDispatcher(dispatcher).dispatch(...)` — a fully arbitrary external call — *before* the escrow map is finalized: assets are pushed to the dispatcher, the predispatch call executes, tokens are pulled back based on dispatcher balance, and only afterward is `_orders[commitment][token] += reducedInputs[i].amount` written [3](#0-2) . The same window exists in `withdraw`, which decrements `_orders[commitment][token]` and then performs a raw `.call{value}` / low-level `token.call(transfer)` to the beneficiary, i.e. state is read, transfer happens, and only then is `_orders` reduced with an interleaved external call in between [4](#0-3) . This is exactly the "value has moved but the ledger hasn't caught up" primitive from the seed report — except here the exploitable window is not a cross-chain block delay but an intra-transaction external call with no reentrancy lock, on a contract that custodies escrowed user funds (`_orders[commitment][token]`) and protocol fees (`TRANSACTION_FEES`).

The canonical EVM sibling closes this exact class of window with `nonReentrant` on every public entrypoint that touches `_orders`. The Tron file, despite copying the identical escrow bookkeeping (`_orders[commitment][token] -= amount` before/after external calls, `TRANSACTION_FEES` release via low-level `.call`), dropped the guard entirely.

### Impact Explanation
Any external call made mid-function (predispatch dispatcher calls, native ETH `.call{value:amount}` to a beneficiary in `withdraw`, or fee-token transfers) can re-enter `placeOrder`, `cancelOrder`, or `withdraw` while the escrow ledger (`_orders`) is in a transiently inconsistent state — mirroring the seed bug's "value not yet reflected in the invariant-checking storage" pattern. Depending on which reentrant call lands (e.g., re-triggering `withdraw` against a commitment whose `_orders[...][token]` decrement hasn't yet been persisted, or re-entering `cancelOrder`'s refund path), this falls squarely into the bounty's "logic attacks" / "unauthorized execution" / possible double-settlement categories on escrowed bridge funds — a direct fund-loss vector for an unprivileged user who crafts their own order's predispatch calldata (no malicious relayer, prover, or admin required).

### Likelihood Explanation
High confidence that the guard is missing (directly grep/read-verified: 9 occurrences of `nonReentrant` in the EVM file vs. 0 in the Tron file). The attacker-controlled entrypoint (`order.predispatch.call`, executed via `ICallDispatcher.dispatch`) is reachable by any user calling `placeOrder` directly — no privileged role, relayer cooperation, or governance action needed to reach the vulnerable code path.

### Recommendation
Add `ReentrancyGuard`/`nonReentrant` to `placeOrder`, `fillOrder`, `cancelOrder`, `withdraw`, and `onGetResponse` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to match the guarantees already present in `evm/src/apps/IntentGatewayV2.sol`, and add a regression test asserting reentrant calls into these functions revert.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron variant) and a malicious `CallDispatcher`-compatible target.
2. Place an order whose `predispatch.call` targets a contract that, when invoked, calls back into `IntentGatewayV2.cancelOrder` (or `placeOrder`) for a second, already-escrowed commitment before the outer `placeOrder` call finishes crediting `_orders[commitment][token]`.
3. Because no `nonReentrant` lock exists, the reentrant call executes against the gateway's current (mid-update) storage state, allowing the escrow ledger to be manipulated relative to actual token custody.

**Caveat / what remains unverified:** I was not able to fully trace, within the available search budget, a step-by-step token-drain amount (i.e., confirm double-crediting bypasses the fresh-`nonce`-per-order commitment binding) purely from static reading — this would need dynamic testing (e.g., a Devin/Foundry session deploying the Tron contract and executing the reentrant PoC above) to confirm the exact drainable amount and beneficiary. The missing guard itself, however, is fully confirmed from the repository code.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-163)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L55-56)
```text
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L381-440)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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
```
