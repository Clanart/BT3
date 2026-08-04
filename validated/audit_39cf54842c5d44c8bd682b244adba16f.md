### Title
Escrow ledger credited from nominal (pre-fee) input amount instead of actual tokens received in Tron `IntentGatewayV2.placeOrder` - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron fork of `IntentGatewayV2.placeOrder` computes the escrow amount credited to `_orders[commitment][token]` from the order's *nominal* input amount reduced only by the protocol fee, but the tokens are pulled via a single `safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` with no post-transfer balance check. For any ERC20 with a transfer fee, tax, or burn-on-transfer mechanism, the gateway's actual token balance increases by less than `order.inputs[i].amount`, while the escrow ledger is still credited as if the full nominal amount (minus only the protocol fee) arrived. This is the same broken invariant as the external report's `transferFrom`/burn issue: a deduction mechanism applied during transfer is not reconciled against what is recorded as "received," so the internal accounting overstates real custody.

### Finding Description
In `placeOrder` (Tron variant), the escrow-crediting loop is: [1](#0-0) 

```
} else {
    for (uint256 i; i < inputsLen;) {
        if (order.inputs[i].amount == 0) revert InvalidInput();
        address token = address(uint160(uint256(order.inputs[i].token)));
        if (token == address(0)) {
            if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
            msgValue -= order.inputs[i].amount;
        } else {
            IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
        }
        // Store reduced amount (after protocol fees) in escrow
        _orders[commitment][token] += reducedInputs[i].amount;
        ...
```

`reducedInputs[i].amount` is derived purely from `order.inputs[i].amount * (10_000 - protocolFeeBps) / 10_000` [2](#0-1) , i.e. it assumes the gateway physically received the full nominal `amount`. There is no balance-before/balance-after measurement to confirm this.

By contrast, the canonical EVM `IntentGatewayV2.sol` was hardened against exactly this class of issue: it snapshots `balBefore`, performs `safeTransferFrom`, and overwrites `order.inputs[i].amount` with the *actual* delta before computing `reducedInputs` and crediting escrow: [3](#0-2) 

The Tron fork does not carry this fix forward, reintroducing the "amount deducted differs from amount credited" bug the external report describes — except here the mismatch corrupts the protocol's own internal escrow bookkeeping rather than a single user's balance.

### Impact Explanation
`_orders[commitment][token]` is the source of truth used later to pay solvers on fill and to refund users on cancellation/timeout. If this value is inflated relative to the contract's real token balance (because a fee-on-transfer/deflationary token was used as an input), then:
- Filling or cancelling that specific order will attempt to pay out more tokens than the contract received for it.
- Because all orders for a given token share the same contract-wide token balance, this shortfall is paid for out of *other users'* escrowed balances of the same token, i.e. legitimate order originators using non-fee tokens can find their escrow under-collateralized and unable to be honored, or a griefer can systematically siphon shared custody by repeatedly placing orders with a fee-on-transfer token to inflate the ledger and then cancelling/filling to drain more than deposited.
This is a direct "loss of funds" / bridge-custody accounting break, matching the required impact class.

### Likelihood Explanation
Any unprivileged user can call `placeOrder` with an arbitrary ERC20 address as the input token — the contract does not allowlist input tokens. Fee-on-transfer and deflationary tokens exist and are usable without any relayer, admin, or governance cooperation, and the same test suite in this repo already demonstrates awareness of and testing for fee-on-transfer tokens against the (fixed) EVM contract (`evm/tests/foundry/IntentGatewayV2SameChainTest.sol`, `testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`), confirming this is a realistic, anticipated token class for this protocol — just not defended against in the Tron deployment path.

### Recommendation
Apply the same actual-balance-delta pattern used in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract: snapshot the gateway's token balance before `safeTransferFrom`, compute the real received amount as `balanceAfter - balanceBefore`, and derive `reducedInputs`/escrow crediting from that real amount rather than from `order.inputs[i].amount`.

### Proof of Concept
1. Deploy a fee-on-transfer ERC20 (e.g., 5% burn on `transfer`) on the Tron-targeted chain.
2. User calls `placeOrder` with `order.inputs[0] = {token: FOT, amount: 1000}`, protocol fee 0 bps for simplicity.
3. Contract calls `safeTransferFrom(user, gateway, 1000)`; gateway's real balance only increases by 950 (5% burned).
4. Contract nonetheless executes `_orders[commitment][token] += reducedInputs[0].amount` where `reducedInputs[0].amount == 1000` (no protocol fee configured), crediting 1000 in escrow while only 950 tokens exist.
5. A second, unrelated user places an order for the same token with a normal (non-fee) transfer, correctly escrowing their full amount.
6. When the first order is filled/cancelled, the contract pays out 1000 tokens for it, which can only be satisfied by consuming part of the second user's escrowed balance — demonstrating cross-user fund loss caused purely by the unreconciled escrow accounting.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-462)
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
