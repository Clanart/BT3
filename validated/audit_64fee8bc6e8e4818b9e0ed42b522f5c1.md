## Title
Missing zero-address validation on `Order.output.beneficiary` in IntentGatewayV2 causes permanent, unrecoverable loss of solver funds - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/IntentGatewayV2.sol` / tron variant)

### Summary
The `1inch-rewards-manager` report shows the class of bug: an address parameter (`_to`) is stored and later used for critical transfers without checking it isn't the zero address, which can brick funds/functions. The local analog is the `beneficiary` field of an `Order`'s `PaymentInfo` in Hyperbridge's Intent Gateway (`IntentGatewayV2` / `IntentsBase` / `ExtrinsicIntents` / `IntrinsicIntents`). The order creator fully controls `order.output.beneficiary` (a raw `bytes32` cast to `address`), and neither `placeOrder` nor the fill paths (`fillOrder` → `_fillCrossChain` / same-chain fill) validate it is non-zero before transferring the solver's output tokens to it.

### Finding Description
`placeOrder` stores the user-supplied `Order.output` (a `PaymentInfo{beneficiary, assets, call}`) unchanged and computes the commitment over it: [1](#0-0) 

When a solver fills a cross-chain order, `_fillCrossChain` derives the payout address directly from the unvalidated field: [2](#0-1) 

and then pushes native value or ERC20 tokens straight to it: [3](#0-2) 

The same unguarded pattern exists in the Tron variant's `withdraw`/fill flow: [4](#0-3) 

Nowhere in the placement, fill, or withdrawal paths is `beneficiary != address(0)` (or `bytes32(0)`) enforced — a repo-wide search for `InvalidBeneficiary`/zero-address guards on this field returns nothing in the intents contracts. Because Solidity's low-level `.call{value: amount}("")` to `address(0)` succeeds (no code, no revert) and ERC-20 `safeTransfer` to `address(0)` is only rejected by tokens that explicitly guard it (not enforced by the gateway), a beneficiary of `address(0)` causes solver output funds to be silently burned rather than delivered to any recoverable account.

### Impact Explanation
Any unprivileged order creator can craft an order with `output.beneficiary = bytes32(0)`. A solver that fills such an order (via `fillOrder`) has their output tokens (native ETH or ERC20, per the token type) sent to `address(0)` and permanently destroyed — while the order is simultaneously marked filled/escrow released to the solver on the source chain, meaning the escrowed input tokens still move to the solver but the counter-value the solver just paid out is unrecoverably burned. This is a direct, one-way loss of funds for the solver, triggered purely by unauthenticated user input (the order struct), matching the bounty's "stealing or loss of funds" and "unauthorized... transaction manipulation" categories. It requires no malicious relayer, prover, or admin — only a malicious/careless order creator and a solver that doesn't independently sanitize third-party order data before filling.

### Likelihood Explanation
Likelihood is moderate-to-high for automated/naive solver bots that fill orders purely based on profitability calculations without validating every field of the `Order` struct pulled from the auction/indexer. Since `placeOrder` takes an arbitrary `Order` from any caller and neither it nor `fillOrder`/`_fillCrossChain`/`_withdraw` reject a zero beneficiary, the malicious order is fully constructible and dispatchable with no privileged capability. The main mitigating factor is that a diligent solver implementation could add its own beneficiary check — but the protocol contracts themselves provide no such guardrail, unlike other address fields in the codebase (e.g., `updateHostParamsInternal` explicitly checks `hostManager`, `handler`, and `consensusClient` against `address(0)`), showing the missing check here is an inconsistency in the codebase's own defensive pattern.

### Recommendation
Add an explicit zero-address check on `order.output.beneficiary` (and generally on `WithdrawalRequest.beneficiary`) at the earliest point of validation: reject in `placeOrder` if `order.output.beneficiary == bytes32(0)`, and defensively re-check in `_fillCrossChain`/`_fillSameChain` and in `_withdraw`/`withdraw` before executing native/ERC20 transfers, mirroring the pattern already used for `hostManager`/`handler`/`consensusClient` in `updateHostParamsInternal` (`evm/src/core/EvmHost.sol`).

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder(order, graffiti)` with `order.output.beneficiary = bytes32(0)` and legitimate `order.inputs` (escrowed normally).
2. A solver observes the order (profitable on paper) and calls `fillOrder(order, options)`.
3. `_fillCrossChain` (or the same-chain equivalent) computes `beneficiary = address(uint160(uint256(bytes32(0)))) == address(0)` and executes `beneficiary.call{value: beneficiaryTotal}("")` (succeeds trivially for native token) or `IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal)` (many ERC20s do not block transfers to zero address).
4. The solver's output payment is burned; a `RedeemEscrow` message is then dispatched and the solver still receives the source-chain escrowed inputs, completing the exploit — the solver has been made to give away value with no counterparty ever receiving it, and no recovery path exists. [5](#0-4)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-382)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }

        emit OrderPlaced({
            user: order.user,
            source: string(order.source),
            destination: string(order.destination),
            deadline: order.deadline,
            nonce: order.nonce,
            fees: order.fees,
            session: order.session,
            predispatch: order.predispatch.assets,
            inputs: reducedInputs,
            beneficiary: order.output.beneficiary,
            outputs: order.output.assets
        });
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-111)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();

            uint256 dust = solverAmount - totalRequired;
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-699)
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
```

**File:** evm/src/core/EvmHost.sol (L581-608)
```text
    function updateHostParamsInternal(HostParams memory params) internal {
        // check the params to prevent the host from getting bricked.
        if (
            params.hostManager == address(0) || address(params.hostManager).code.length == 0
                || !IERC165(params.hostManager).supportsInterface(type(IApp).interfaceId)
        ) {
            // otherwise cannot process new cross-chain governance requests
            revert InvalidHostManager();
        }

        if (
            params.handler == address(0) || address(params.handler).code.length == 0
                || !IERC165(params.handler).supportsInterface(type(IHandlerV2).interfaceId)
        ) {
            // otherwise cannot process new datagrams
            revert InvalidHandler();
        }

        if (
            params.consensusClient == address(0) || address(params.consensusClient).code.length == 0
                || !IERC165(params.consensusClient).supportsInterface(type(IConsensusV2).interfaceId)
        ) {
            // otherwise cannot process new consensus datagrams
            revert InvalidConsensusClient();
        }

        // otherwise cannot process new cross-chain governance requests
        if (keccak256(params.hyperbridge) == keccak256(bytes(""))) revert InvalidHyperbridgeId();
```
