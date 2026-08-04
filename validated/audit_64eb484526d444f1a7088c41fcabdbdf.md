## Finding: Unbounded `order.inputs` array length enables permanent fund lock in IntentGatewayV2 escrow release

The Radiant-style bug (unbounded iteration over a user-populated storage array causing gas exhaustion and locked funds) has a direct analog in Hyperbridge's Intents contracts.

### Title
Unbounded `Order.inputs`/`Order.output.assets` array size permits gas-exhaustion fund lock in `IntentGatewayV2`/`IntentsBase` escrow settlement - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/IntentGatewayV2.sol`, `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder()` only validates that `order.inputs.length != 0` (`InvalidInput()` on empty array) but imposes no upper bound on the number of input tokens (or output assets) an order may contain. [1](#0-0) [2](#0-1) 

Every settlement path that later releases the escrowed tokens — `_withdraw()` (fill/cancel on `IntentsBase.sol`) and `withdraw()` in the Tron `IntentGatewayV2.sol` — iterates over `body.tokens`, which mirrors `order.inputs` 1:1, performing an external transfer per element: [3](#0-2) [4](#0-3) 

Same-chain cancellation (`_cancelSameChain`) and cross-chain cancellation (`_cancelFromSource`/`_cancelFromDest`) also loop over the full `order.inputs` array before calling `_withdraw`: [5](#0-4) [6](#0-5) 

### Finding Description
A user creates an order via `placeOrder()` with an arbitrarily large `inputs` array (e.g. hundreds of distinct ERC-20 token addresses, even fabricated/duplicate-check-passing addresses of contracts they control), escrowing tokens for each. There is no cap on `inputsLen`, unlike the duplicate-output check which only guards `output.assets`.

Every code path that eventually releases this escrow — a solver's `fillOrder` (full fill), the order creator's same-chain `cancel`, the cross-chain `RefundEscrow`/`RedeemEscrow` `onAccept` handler invoked by the trusted `IIsmpModule` callback from the host, or the GET-response-driven `onGetResponse` cancellation flow — must loop over every entry of `body.tokens`/`order.inputs`, performing a `safeTransfer`/native `call` per token. Because these are all single-transaction, non-resumable loops with no batching/pagination, once the array is large enough that gas required exceeds the block gas limit (or the fixed gas stipend forwarded by the ISMP dispatch/callback path), the transaction can never succeed. Since the `onAccept` handler is the only path that finalizes the order and releases escrow across chains, and there is no fallback partial-iteration or reduced-batch retry, the escrowed tokens become permanently stuck in the contract with no way to reduce the array size after the order commitment has already been hashed and dispatched.

This mirrors the reported pattern exactly: the storage/array size is fully attacker-/user-controlled at creation time, with no cap enforced (`Radiant` allowed unlimited `userLocks`/`userEarnings` entries; `IntentGatewayV2` allows unlimited `order.inputs`/`output.assets` entries), and the same array is unconditionally, fully iterated during the value-release codepath (`_cleanWithdrawableLocks`/`withdraw` vs. `_withdraw`/`withdraw`).

### Impact Explanation
An order's escrowed funds (potentially the full deposited amount across many tokens) can become permanently unrecoverable because the only functions capable of releasing escrow (`fillOrder`, `cancel`, `onAccept`) cannot complete within the available gas envelope. This is a direct loss-of-funds condition, not a routine node/relayer resource DoS — the funds sit in the contract with no code path left to extract them once the array is oversized.

### Likelihood Explanation
Likelihood is low-to-medium: it requires a user (or self-targeting attacker) to intentionally build an order with an excessive number of input tokens, or a token list large enough that combined `safeTransfer`/`call` overhead per entry pushes total gas past the block limit or ISMP callback gas stipend. No malicious relayer, prover, or governance actor is required — it is reachable purely through the public, unprivileged `placeOrder()` entrypoint.

### Recommendation
Enforce a maximum length for `order.inputs` and `order.output.assets` (and equivalently for any `WithdrawalRequest.tokens` derived from them) in `placeOrder()`, mirroring the duplicate-token check already present for outputs. Reject orders whose input/output array size could plausibly exceed a conservative per-call gas budget for the settlement loop.

### Proof of Concept
1. Call `IntentGatewayV2.placeOrder()` with `order.inputs` containing e.g. 300+ distinct valid ERC-20 tokens (no length check blocks this) — see `evm/src/apps/IntentGatewayV2.sol:162-163`.
2. Order is escrowed and its commitment dispatched cross-chain.
3. A solver later calls `fillOrder` (or the order creator calls `cancel`), which invokes `_withdraw`/`withdraw`, looping over all 300+ entries with a `safeTransfer` or native `call` each — see `evm/src/apps/intentsv2/IntentsBase.sol:390-410` / `evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`.
4. The transaction (or the host-invoked `onAccept` callback with a bounded gas stipend) runs out of gas and reverts every time it is attempted, leaving the escrowed tokens permanently locked in the gateway contract with no alternate recovery function.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-163)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-334)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
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
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-223)
```text
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
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
