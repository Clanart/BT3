Confirmed the analog: this is a real, locally-provable instance of the exact SafEth bug class (single external-call failure inside a multi-item loop reverting the entire operation), applied to escrow release rather than derivative staking.

### Title
Permanent solver fund loss via malicious multi-token order input causing atomic `_withdraw` DoS - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase._withdraw()` iterates over an order's escrowed token list and releases each one to the beneficiary in a single atomic loop [1](#0-0) . There is no isolation between per-token transfers: if any single token in `body.tokens` reverts on transfer, the entire withdrawal reverts and none of the other, unrelated tokens escrowed for that order can ever be released. Since order inputs are freely chosen by the order creator at `placeOrder` time, an attacker can construct an order whose input set mixes legitimate value with one deliberately-unspendable token, permanently DoS-ing release of the whole escrow — this is the same "all-or-nothing external call in a loop" flaw the external SafEth report describes for `stake()/unstake()/rebalanceToWeights()`.

### Finding Description
`_withdraw` decrements escrow accounting and transfers out each token unconditionally in one loop: [2](#0-1) 
For ERC-20 tokens it uses `SafeERC20.safeTransfer`, which reverts the whole call if the underlying `transfer` returns false or reverts (e.g. a blacklist-style token such as USDT/USDC-style freeze, a pausable token, or a token the order-creator deploys specifically to revert for arbitrary solver/beneficiary addresses).

This function is reached from the cross-chain fill flow: the solver calls `fillOrder` and, in `_fillCrossChain`, pays out the *output* tokens to the beneficiary up front, then dispatches a `RedeemEscrow` message back to the order's source chain so the solver can later collect the escrowed *input* tokens: [3](#0-2) 
When that message lands on the source chain, `onAccept` eventually calls `_withdraw`/`withdraw` releasing `order.inputs` to the solver as beneficiary. Because `order.inputs` is attacker-controlled at order-placement time, the order creator can include one poisoned token alongside legitimate ones (e.g. USDC + a custom token that reverts transfers to non-whitelisted addresses). The solver has no way to know this in advance since order composition is arbitrary, and the fill is "all or nothing" (no partial fill support for cross-chain per the code comments).

Unlike `EvmHost.dispatchIncoming`, which deliberately wraps the app callback in a low-level `.call` and rolls back only the receipt on failure so the message can be retried [4](#0-3) , retrying does not help here: the underlying cause (the malicious token permanently reverting for that specific beneficiary) is deterministic and does not resolve with time or retries, so the message can never be delivered successfully, and the receipt/commitment bookkeeping keeps resetting on every retry attempt.

The identical pattern also exists in the same-chain cancel/refund path (`IntrinsicIntents._cancelSameChain` → `_withdraw`) and in the Tron port's `withdraw()` [5](#0-4) .

### Impact Explanation
The solver has already irrevocably transferred the full output value to the beneficiary before the input-escrow redemption is attempted (`_fillCrossChain` pays outputs synchronously, escrow redemption happens asynchronously via a later cross-chain message). If the order's input token set contains one poisoned/blacklisting token, the entire `_withdraw` call for *all* input tokens in that order permanently reverts — the solver's already-paid-out value is never compensated by the escrowed inputs, none of which (including unrelated, perfectly normal ERC-20/ETH inputs bundled in the same order) can ever be released. This is a direct, unrecoverable loss of funds for the solver and a permanent lock of escrowed assets in the contract, matching the bounty's "stealing or loss of funds" / "logic attacks" categories.

### Likelihood Explanation
Likelihood is high for any attacker willing to place a malicious order: no privileged role, relayer, or governance action is required. The attacker only needs to (1) deploy or reuse an ERC-20 with a transfer-side restriction (blacklist, pausable, or reverting behavior keyed on the counterparty address or authorization state) and (2) include it as one of several `order.inputs` in an otherwise attractive cross-chain intent. A solver who fills it based on the visible output-side economics has no way to pre-verify at fill time that the escrow redemption will later succeed for their own address, since order-side inputs are chosen entirely by the order creator.

### Recommendation
Do not process multiple heterogeneous token releases atomically. Either: (1) isolate each token transfer with a try/catch (low-level `call`) inside the loop in `_withdraw`, skipping/recording failures instead of reverting the whole function, similar to the retry-safe pattern already used in `EvmHost.dispatchIncoming`; or (2) allow partial/per-token settlement so a beneficiary can claim the successfully-transferable tokens even when one token in the same order is permanently unspendable, with a separate accounting path (e.g., a "stuck balance" ledger) for the failing token so it doesn't block release of the rest of the escrow.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20 whose `transfer()` reverts unconditionally for any address other than the attacker's own address (a simple blacklist mapping defaulted to block everyone else).
2. Attacker calls `placeOrder` with `order.inputs = [ {token: USDC, amount: 1000} , {token: EvilToken, amount: 1} ]` and an attractive `order.output` (e.g., 1000 USDC-equivalent on another chain), escrowing both tokens into the gateway.
3. A solver fills the order cross-chain via `fillOrder`, paying the full output amount to `beneficiary` in `_fillCrossChain` [6](#0-5) , then the gateway dispatches `RedeemEscrow{tokens: order.inputs, beneficiary: solver}` back to the source chain.
4. On the source chain, the incoming message triggers `_withdraw` with `body.tokens = [USDC, EvilToken]` and `beneficiary = solver`. The USDC transfer would succeed, but the loop first (or eventually) hits `EvilToken.transfer(solver, 1)`, which reverts unconditionally for the solver's address, causing `SafeERC20.safeTransfer` to bubble up and revert the entire `_withdraw` call [7](#0-6) .
5. Because the failure is deterministic (not a transient/liveness issue), every retry of the incoming message replays the same revert. The solver never receives the 1000 USDC they are owed despite having already paid the full output value — permanent fund loss, and both the USDC and EvilToken remain locked in escrow forever.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
```text
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-147)
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

            if (dust > 0) {
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            }

            if (token == address(0)) {
                if (msgValue < solverAmount) revert InsufficientNativeToken();
                uint256 beneficiaryTotal = totalRequired + beneficiaryShare;
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, totalRequired + beneficiaryShare);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
            if (protocolShare > 0) emit DustCollected(token, protocolShare);
            outputFills[i] = TokenInfo({token: outputToken, amount: totalRequired});
        }

        _execute(order, outputsLen);

        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
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
