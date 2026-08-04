## Analysis

**Reducing the external report to its core primitive:** The LSP8 bug is that a derivative/ported contract (`LSP8Burnable`) failed to correctly mirror the corrected reference implementation (`LSP8IdentifiableDigitalAsset`) that its sibling extensions use, silently dropping guard logic relied on elsewhere. The exploitable local analog I found is exactly this pattern: the Tron port of the IntentGateway (`evm/tron/contracts/apps/IntentGatewayV2.sol`) diverges from the canonical, already-hardened EVM implementation (`evm/src/apps/intentsv2/IntrinsicIntents.sol` / `IntentsBase.sol`) in how it computes the escrow amount to release during order cancellation, reintroducing a broken invariant that the reference implementation had already fixed.

### Title
Cancellation on the Tron IntentGateway uses unreduced order amounts instead of tracked escrow, permanently locking user funds when a protocol fee is configured - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.cancelOrder()` on the Tron deployment builds the `WithdrawalRequest.tokens` array from `order.inputs` (the user's originally-submitted, pre-fee amounts) rather than from the actual per-token escrowed balance in `_orders[commitment][token]` (the post-protocol-fee amount that was really deposited). The canonical EVM implementation was already fixed to use the tracked escrow balance instead.

### Finding Description
At `placeOrder()`, when `protocolFeeBps > 0`, the escrow ledger is credited with the *reduced* amount: [1](#0-0) 
while the caller-supplied `order.inputs` still holds the full, pre-fee amount.

Every cancellation path then constructs the `WithdrawalRequest` from `order.inputs` (unreduced) instead of `_orders[commitment][token]` (the real escrow):
- Same-chain cancel: [2](#0-1) 
- Cross-chain cancel-from-source (encoded into the GET context, later consumed by `onGetResponse`): [3](#0-2) 
- Cross-chain cancel-from-destination (dispatched as `RefundEscrow`, consumed by `onAccept`): [4](#0-3) 

All three converge on `withdraw()`, which only checks that the escrow is non-zero (not that it covers `amount`), transfers the caller-controlled `amount` to the beneficiary, and only then subtracts it from the ledger with Solidity 0.8 checked arithmetic: [5](#0-4) 

Because `amount` (from `order.inputs`, pre-fee) is strictly greater than `_orders[body.commitment][token]` (the reduced escrow actually credited) whenever a protocol fee is configured, the line `_orders[body.commitment][token] -= amount;` underflows and reverts (Panic 0x11) on every single cancellation attempt — same-chain, cross-chain-from-source, and cross-chain-from-destination.

This contrasts with the corrected EVM reference implementation, which computes the refund from the actual tracked escrow balance rather than the raw order amount: [6](#0-5) 

### Impact Explanation
Whenever the Tron `IntentGatewayV2` deployment is configured with `protocolFeeBps > 0` (a normal, expected governance configuration exposed via `setParams`/`UpdateParams`), `cancelOrder()` unconditionally reverts for every order on every cancellation path. Since escrowed input tokens can only leave the contract via a successful fill or a successful cancel, any order that a solver never fills becomes permanently unrecoverable — a direct, unprivileged loss/lock of user funds triggered purely by normal protocol usage, with no relayer, prover, or admin compromise required.

### Likelihood Explanation
High. This does not require any malicious peer, relayer, governance actor, or crafted proof — it triggers deterministically for any ordinary user placing and then attempting to cancel an order under the standard, documented `protocolFeeBps` fee mechanism. It is a pure logic/arithmetic defect reachable directly through the public `placeOrder`/`cancelOrder` entrypoints.

### Recommendation
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, build the `WithdrawalRequest.tokens` for every cancellation path (same-chain, cross-chain-from-source context, cross-chain-from-destination `RefundEscrow` body) from the actual `_orders[commitment][token]` balances rather than from `order.inputs`, mirroring the fix already present in `IntrinsicIntents._cancelSameChain` on the EVM side.

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` and call `setParams` with `protocolFeeBps > 0` (e.g., 100 = 1%).
2. User calls `placeOrder()` with input token USDC, amount = 1000 USDC. `_orders[commitment][USDC]` is credited with `990` (after 1% fee); `order.inputs[0].amount` remains `1000` in the struct the user later re-submits.
3. Before any solver fills, user calls `cancelOrder(order, options)` for the same-chain path.
4. Inside `withdraw()`, `amount = 1000` (from `order.inputs`) is transferred out via `token.call(transfer(...))`, then `_orders[commitment][USDC] -= 1000` executes against a stored value of `990`, underflows, and the whole transaction reverts.
5. Repeat for any order, indefinitely — cancellation can never succeed while `protocolFeeBps > 0`, permanently locking every unfilled order's escrow.

**Uncertainty note:** I was unable to fully inspect whether `evm/tron/contracts/apps/IntentGatewayV2.sol` contains a `fillOrder` function elsewhere in the repo (it was not present in the file content retrieved), so I cannot confirm whether locked orders could alternatively be released via a solver fill in that deployment; this does not affect the cancellation-underflow finding itself, which is fully supported by the code shown above.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-368)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L550-551)
```text
            bytes memory context =
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L588-591)
```text
            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
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
