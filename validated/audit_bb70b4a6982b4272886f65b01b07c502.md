## Title
Shared `CallDispatcher` custody allows theft of stranded tokens via `IntentGatewayV2.placeOrder` predispatch sweep - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder`'s predispatch flow escrows a user's tokens by first running arbitrary, user-supplied calldata (`order.predispatch.call`) through a single, protocol-wide `CallDispatcher` contract (`_params.dispatcher`), then sweeping whatever ERC20 balance that shared contract currently holds for each `order.inputs[i].token` into the gateway. The sweep is based purely on the *current total balance* of the shared dispatcher for that token address — never on a delta tied to what the caller's own predispatch call actually produced, and never scoped to tokens the caller actually deposited. Any ERC20 balance left stranded on the shared `CallDispatcher` by an unrelated, earlier transaction (a user's own predispatch mistake, multi-hop swap dust, or any other app that reuses this same deployed dispatcher) can therefore be claimed by any subsequent caller simply by listing that token as one of their own order's inputs, then immediately cancelling the order (same-chain path) to redeem it as a "refund." This is the direct analog of the Wido `LibCollateralSwap` bug: no check that swept funds actually correspond to *this* execution's own swap output, so mis-routed/stranded tokens become stealable by whoever next references that token address.

### Finding Description
In the predispatch branch of `placeOrder`: [1](#0-0) 

the caller's `predispatch.assets` are pushed to the shared `dispatcher` and `order.predispatch.call` (arbitrary calldata chosen by the caller) is executed on it via: [2](#0-1) 

Then, for each of the order's declared input tokens, the code reads the dispatcher's **entire current balance** of that token and sweeps all of it to the gateway: [3](#0-2) 

and finally computes `received` as the delta on the gateway's own balance (which equals the full swept amount), crediting up to `order.inputs[i].amount` to the caller's escrow and any excess as protocol "dust": [4](#0-3) 

The `dispatcher` (`_params.dispatcher`) is not an ephemeral, per-call contract — it is a single persistent `CallDispatcher` deployment shared by every order and, per the docs, by other Hyperbridge apps as well: [5](#0-4) 

`CallDispatcher.dispatch` executes arbitrary calls in its own context and can end up holding any ERC20 balance temporarily; nothing forces that balance to be flushed only to the caller who produced it. If a caller's own `predispatch.call` produces an unintended/extra token that is *not* included in that same order's `order.inputs` list (e.g., a multi-hop swap or DEX aggregator leaves intermediate-token dust, or the caller made the exact "wrong asset" mistake described in the Wido report), that token balance is never swept and remains sitting on the shared dispatcher indefinitely — the `placeOrder` transaction itself still succeeds as long as the *declared* inputs were satisfied.

Any subsequent, unrelated caller can then place a trivial order (minimal `predispatch.assets`, a no-op `predispatch.call`) whose `order.inputs` lists the stranded token with `amount` set to the known stranded balance. The balance check `balance < requiredAmount` passes (the balance already contains the stranded funds), and the full amount is credited as this new caller's own escrowed input — with no verification that the caller's own predispatch execution actually produced it. The caller, being `order.user` of their own order, can immediately invoke `cancelOrder` (same-chain path) to redeem the escrow back to themselves: [6](#0-5) [7](#0-6) 

completing the theft of tokens that never belonged to them.

### Impact Explanation
This is direct fund theft through a public entrypoint (`placeOrder` + `cancelOrder`), requiring no privileged role, relayer, prover, or governance action — only a stranded token balance on the shared, protocol-owned `CallDispatcher`. It matches the bounty's "stealing or loss of funds" and "bridge custody... must move exactly once and only to the rightful beneficiary and amount" criteria: the custody contract's balance is not bound to the specific execution that produced it, so an attacker's unrelated order can capture and withdraw funds that were never escrowed for them.

### Likelihood Explanation
Likelihood depends on tokens becoming stranded on the shared dispatcher — plausible whenever a user's `predispatch.call` (multi-hop swap, aggregator route, or a genuine "wrong asset" mistake as in the original report) yields a token not listed in that same order's `order.inputs`, or whenever any other app sharing the same `CallDispatcher` deployment leaves residual balance. Once stranded, exploitation requires only a single low-cost `placeOrder`/`cancelOrder` pair by an opportunistic attacker monitoring the dispatcher's token balances — no race condition, front-running, or special privilege needed.

### Recommendation
Bind the amount swept from the shared dispatcher to what the *current* transaction's own predispatch call actually produced, not the dispatcher's total balance: snapshot the dispatcher's per-token balance immediately before dispatching `order.predispatch.call`, and only sweep the delta produced by that specific call (reverting if a token is untracked/unlisted rather than allowing it to be silently absorbed by a future, unrelated caller). Additionally, ensure every token a predispatch call can output is required to be enumerated and swept in the same transaction (so nothing is ever left on the shared dispatcher), and consider isolating dispatcher usage per-call (e.g., ephemeral proxy/clone) rather than reusing a single persistent shared contract for value-bearing operations.

### Proof of Concept
1. Deploy/rely on the existing shared `CallDispatcher` at `_params.dispatcher`.
2. Transaction 1 (can be an honest user's mistake, or attacker-seeded): call `IntentGatewayV2.placeOrder` with `predispatch.assets = [tokenY: X]`, `predispatch.call` = a multi-step swap that converts `tokenY` into `tokenA` (the order's declared, required input) **and** `tokenZ` (an extra output not present in `order.inputs`). The transaction succeeds because `order.inputs` (only `tokenA`) is satisfied; `tokenZ` remains on the shared `dispatcher` since it's never referenced in the sweep loop.
3. Transaction 2 (attacker, any address): call `placeOrder` with a minimal `predispatch.assets` (e.g., 1 wei of a cheap token) and a no-op `predispatch.call`, but set `order.inputs = [tokenZ: strandedAmount]`. The sweep loop at `evm/src/apps/IntentGatewayV2.sol:242-256` reads `IERC20(tokenZ).balanceOf(dispatcher)` — which is still `strandedAmount` — passes the `balance < requiredAmount` check, and transfers the entire stranded balance to the gateway; `order.inputs[0].amount` becomes `strandedAmount`, all credited to the attacker's own order's escrow.
4. Attacker immediately calls `cancelOrder` (same-chain path, `_cancelSameChain` → `_withdraw`), receiving the full `strandedAmount` of `tokenZ` back to their own address — funds they never actually deposited.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L203-227)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L242-256)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L260-280)
```text
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

**File:** evm/src/utils/CallDispatcher.sol (L25-62)
```text
contract CallDispatcher is ICallDispatcher {
    /**
     * @dev error thrown when the target is not a contract.
     */
    error NotContract(address target);

    /**
     * @dev error thrown when a call fails.
     */
    error CallFailed(address target, bytes result);

    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-180)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
```

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
