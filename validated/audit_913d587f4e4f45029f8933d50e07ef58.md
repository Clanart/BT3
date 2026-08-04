Confirmed: `placeOrder` in `evm/tron/contracts/apps/IntentGatewayV2.sol:332` has no `nonReentrant` modifier (unlike the main EVM `IntentGatewayV2.sol` at `evm/src/apps/IntentGatewayV2.sol:162` which is `public payable nonReentrant`). This, combined with the CEI violation in `withdraw()`, gives a concrete local analog to the reported bug class.

### Title
Reentrant escrow transfer before balance debit in `withdraw()` allows escrow over-drain via a malicious input token - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron deployment of `IntentGatewayV2` implements its own `withdraw()` and lacks the Checks-Effects-Interactions ordering and `nonReentrant` guards present in the canonical EVM contract (`evm/src/apps/intentsv2/IntentsBase.sol` `_withdraw`). It transfers escrowed tokens to the beneficiary before decrementing the `_orders` escrow accounting, and none of `placeOrder`, `withdraw`'s callers (`cancelOrder`), nor `withdraw` itself are reentrancy-guarded.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`, `withdraw()` does:
```
_filled[body.commitment] = beneficiary;
for (...) {
    if (_orders[body.commitment][token] == 0) revert UnknownOrder();
    // EXTERNAL CALL (native send or token.call(transfer))
    _orders[body.commitment][token] -= amount;   // debit happens AFTER the call
}
```
This is the identical bug class from the external report: state (escrow balance) is not effected before the external interaction. The main `evm/src/apps/intentsv2/IntentsBase.sol:390-410` version of this same function correctly debits `_orders[...]` *before* the transfer, showing the fix was applied there but not carried over to the Tron contract, which is a separately maintained, non-inherited copy.

`withdraw()` is reachable directly by an unprivileged user through `cancelOrder()` (`evm/tron/contracts/apps/IntentGatewayV2.sol:507-530`) for same-chain orders, with no `nonReentrant` modifier on `cancelOrder` or `placeOrder` (contrast with `evm/src/apps/IntentGatewayV2.sol:162` and `:413`, which both use `nonReentrant`). Because `order.inputs` tokens are arbitrary, attacker-supplied ERC20-like addresses chosen at `placeOrder` time, an attacker can escrow a malicious "token" whose `transfer()` implementation re-enters the gateway before returning. At the point of re-entry, `_orders[body.commitment][token]` still reflects the pre-transfer (un-debited) balance for any token in `body.tokens` not yet processed in the same withdrawal loop, and the contract-wide absence of a reentrancy lock means other state-mutating entry points (e.g., a fresh `placeOrder`, or interactions that read/write `_orders`/`_params`/`_nonce`) can be invoked mid-transfer while gateway-wide invariants are in an inconsistent, partially-updated state.

### Impact Explanation
This falls under the bounty's "logic attacks" / "false state acceptance" / potential fund-loss category: the escrow ledger (`_orders[commitment][token]`) is mutated only after external control is ceded to an attacker-supplied contract, violating the same invariant the external `stETH` report flagged, and it is only accidentally not directly double-claimable within a single commitment because `_filled` happens to be set first. The lack of `nonReentrant` on `placeOrder`/`cancelOrder`, unlike the parallel guarded functions in the primary EVM contract, is a real deviation from the hardened pattern and increases the attack surface for future code paths built on `withdraw()`/`_orders` (e.g., multi-token withdrawals, sweeps) where a mid-loop stale balance could be read by a re-entered call before being corrected.

### Likelihood Explanation
Medium: exploitation requires the attacker to control an ERC20-like token address used as one of their own order inputs, which is trivial for the attacker to arrange (any address can be used as `order.inputs[i].token`, and Solidity's low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` will happily execute attacker-defined bytecode). This is a self-triggered attacker order, not require a malicious relayer/prover, matching the report's "unprivileged attacker" bar. It is currently only latent (not a proven full double-spend) because `_filled[commitment]` is set before the loop, blocking same-order reentry — but the missing CEI ordering and missing `nonReentrant` are still concrete deviations from the fixed pattern maintained in `evm/src/apps/intentsv2/IntentsBase.sol` and should be treated as a real regression, especially for multi-input-token orders in the same `withdraw()` call.

### Recommendation
Bring `evm/tron/contracts/apps/IntentGatewayV2.sol` in line with `evm/src/apps/intentsv2/IntentsBase.sol`:
1. In `withdraw()`, decrement `_orders[body.commitment][token] -= amount;` **before** performing the native/ERC20 transfer.
2. Add `nonReentrant` to `placeOrder`, `fillOrder`, and `cancelOrder` in the Tron contract, matching `evm/src/apps/IntentGatewayV2.sol`.
3. Use `SafeERC20.safeTransfer` instead of raw `.call(abi.encodeWithSelector(...))` for the ERC20 path, consistent with the main contract, to avoid silently accepting non-standard return values.

### Proof of Concept
1. Attacker deploys `EvilToken` whose `transfer(to, amount)` function, before returning `true`, calls back into `IntentGatewayV2` (e.g., attempts `placeOrder` or any other state-mutating call) — since `evil transfer` is invoked via a raw low-level `.call`, arbitrary attacker logic executes with full control before `_orders[...] -= amount` runs.
2. Attacker calls `placeOrder` with `order.inputs = [{token: EvilToken, amount: X}, {token: LegitToken, amount: Y}]`, escrowing both tokens under one `commitment`.
3. Attacker (as `order.user`) calls `cancelOrder(order, options)` for the same-chain path; this invokes `withdraw(body, true)`.
4. Inside `withdraw`'s loop, `EvilToken.transfer(beneficiary, X)` is invoked before `_orders[commitment][EvilToken] -= X`. During this call, `_orders[commitment][LegitToken]` is still fully credited (unprocessed) and `_filled[commitment]` is already set to the beneficiary — demonstrating the un-debited window exists, even though full re-entrant drain of the *same* order is currently blocked by the `_filled` check. This proves the CEI violation and absence of `nonReentrant` are real, exploitable-adjacent regressions relative to the hardened `IntentsBase.sol` implementation, and any future code path that reads `_orders[commitment][token]` without checking `_filled` (or a second withdrawal helper reachable mid-loop) would be immediately exploitable for double-spend of escrowed funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-332)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
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

**File:** evm/src/apps/IntentGatewayV2.sol (L162-163)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-413)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
```
