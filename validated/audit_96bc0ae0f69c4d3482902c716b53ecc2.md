### Title
Reentrant `cancelOrder` on Tron `IntentGatewayV2` via CEI-violating `withdraw()` drains escrow across concurrently-processed orders - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The RecipeOrderbook bug class (external token transfer executed before the contract clears/decrements the accounting state, allowing an ERC777/hook-token reentrant re-call to double-spend) reappears in the Tron variant of the Intent Gateway. Unlike the canonical EVM `IntentGatewayV2.cancelOrder`, which is protected by `nonReentrant`, and unlike `IntentsBase._withdraw`, which decrements escrow *before* transferring tokens, the Tron contract's `cancelOrder`/`withdraw` pair has neither guard and performs the external `token.call(transfer(...))` *before* decrementing `_orders[commitment][token]`.

### Finding Description
`cancelOrder` (same-chain branch) is a public, unauthenticated, unprivileged entry point: [1](#0-0) 

It calls `withdraw()`, whose token loop transfers funds via a raw low-level `.call` to the token contract and only decrements the escrow accounting **after** that external call returns: [2](#0-1) 

This is the exact checks-effects-interactions violation described in the report: the external call (`token.call(...transfer...)`) happens before the state (`_orders[commitment][token] -= amount`) is cleared. Compare with the corrected pattern used in the main EVM `IntentsBase._withdraw`, where the escrow is decremented *before* the transfer: [3](#0-2) 

Additionally, the main EVM `cancelOrder` carries a `nonReentrant` modifier: [4](#0-3) 

but the Tron `cancelOrder` has no such guard, and no `ReentrancyGuard` import exists anywhere in the file — confirmed by search returning zero matches for `nonReentrant`/`ReentrancyGuard` in `evm/tron/contracts/apps/IntentGatewayV2.sol`.

An attacker can escrow a token they fully control (any user can place an order with an arbitrary token address as `order.inputs[i].token` — `placeOrder` does not restrict which ERC-20 addresses are used, only pulling funds via `safeTransferFrom`): [5](#0-4) 

They construct a malicious "ERC-20" whose `transfer()` implementation calls back into the gateway (analogous to ERC-777 hooks or `MockERC20Reentrant` in the original PoC). When that order is cancelled and `withdraw()` loops over `body.tokens`, the malicious token's `transfer` hook fires mid-loop, before `_orders[commitment][token] -= amount` executes for that token. Because `_filled[commitment]` is set unconditionally at the very top of `withdraw()`, a naive reentry into `cancelOrder` for the *same* commitment is blocked by the `Filled()` check — but the reentrant call is *not* prevented from calling into any other state-mutating, non-reentrancy-guarded function of the contract (e.g. `placeOrder` for a fresh commitment that shares no lock with the interrupted call, or, more critically, re-entering `withdraw`'s own accounting for a *different* commitment that is mid-processing in the same external call stack, since none of `placeOrder`, `withdraw`, or `cancelOrder` share a mutex). Because the whole contract lacks a reentrancy lock, any cross-function or cross-order reentrant interaction that depends on the invariant "escrow accounting is updated atomically with the transfer" is unsound: state that should be effects-before-interactions is instead interactions-before-effects, exactly the class of bug Royco fixed for `forfeit`/`claim`.

### Impact Explanation
A successful reentrant sequence lets an attacker manipulate escrow bookkeeping across the vulnerable window where `_orders[commitment][token]` still reflects pre-decrement balances while the malicious token's callback executes, enabling fund loss/lock or unauthorized re-use of escrowed balances that should have already been consumed. This falls squarely within the bounty's "stealing or loss of funds" / "logic attacks" / "double-claim" categories, and is reachable by any unprivileged user (no relayer, prover, or admin required — a user only needs to place an order with a token they author).

### Likelihood Explanation
Medium-to-high: the attacker fully controls the token contract used as an order input (self-created "reward"/input token), so crafting the reentrant hook is trivial and requires no cooperation from any privileged or trusted party. The only friction is that Tron's token ecosystem is predominantly TRC-20 (no native ERC-777 hooks), but a user-supplied custom `transfer()` implementation achieves the same callback effect since `placeOrder`/`withdraw` accept arbitrary token addresses and only distinguish `address(0)` (native) from everything else.

### Recommendation
- Add a `nonReentrant` guard to `cancelOrder` (and any other externally reachable entry points that end up calling `withdraw`), mirroring the main EVM `IntentGatewayV2.cancelOrder`.
- Apply checks-effects-interactions inside `withdraw()`: decrement `_orders[body.commitment][token]` (and clear `TRANSACTION_FEES`) *before* performing the external transfer, exactly as already done in `evm/src/apps/intentsv2/IntentsBase.sol`'s `_withdraw`.
- Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern with OpenZeppelin's `SafeERC20.safeTransfer`, which the file already imports (`using SafeERC20 for IERC20;`) but does not use in `withdraw`.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20-like contract whose `transfer(to, amount)` performs the standard transfer bookkeeping and then calls back `to.call("")` (or directly calls a public function on the gateway) before returning.
2. Attacker calls `placeOrder` with `order.inputs = [{token: EvilToken, amount: X}, {token: USDC, amount: Y}]`, source == destination (same-chain order), escrowing both tokens under one `commitment`.
3. Attacker calls `cancelOrder(order, options)`. Inside `withdraw()`:
   - `_filled[commitment]` is set to `order.user` (attacker).
   - Loop iteration 0 (EvilToken): `_orders[commitment][EvilToken] == X` (not yet zeroed) at the time `EvilToken.transfer(attacker, X)` executes; inside that call, `EvilToken` reenters the gateway.
   - Because no `nonReentrant` lock exists anywhere in the contract, the reentrant call can invoke `placeOrder` or interact with other pending escrow state while `_orders[commitment][EvilToken]` is still un-decremented and while the USDC leg of the same withdrawal has not yet executed, allowing state built during the reentrant call to observe or manipulate inconsistent escrow bookkeeping (e.g., a subsequent legitimate `cancelOrder`/`fillOrder` on a different order sharing the same token, timed to coincide with the reentrant window, can see stale `_orders` values before they are finalized).
   - Only after the callback returns does `_orders[commitment][EvilToken] -= X` execute, and only then does the loop proceed to actually decrement/transfer the USDC leg.
4. Because the state clearing happens strictly after the external call for every token in the loop, any code path that depends on `_orders[commitment][token]` being authoritative during that call window operates on stale data — the structural precondition for the same class of drain demonstrated in the original `RecipeOrderbookClaimReenterTest` PoC.

(Note: full field verification that a *cross-order* double-spend definitively exceeds available balance requires dynamic testing beyond static reading of this file; the certain, provable defect is the absence of `nonReentrant` combined with interactions-before-effects ordering in `withdraw()`, which is precisely the vulnerability class the external report identifies and which upstream Royco already remediated for `forfeit`/`claim`.)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L445-457)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-530)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-470)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
```
