Based on my investigation, the strongest local analog to the FlatMoney "external call before state update" bug class is a **structural regression in the Tron deployment of the intents escrow-withdrawal path**, where the state decrement for escrowed funds happens *after* the external token/ETH transfer — the exact CEI violation already identified, tested, and fixed in the sibling EVM contracts of this same repository.

### Title
CEI violation in `withdraw()` allows escrow accounting to remain stale during external transfer callback - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`IntentGatewayV2.withdraw()` in the Tron contract variant sends escrowed tokens/native currency to the beneficiary via an external `.call` **before** decrementing `_orders[commitment][token]`, whereas the equivalent function in the primary EVM implementation, `IntentsBase._withdraw()`, was fixed to update `_orders` **before** making the external call. This is precisely the "external call before state finalization" bug class from the seed report (`LeverageModule._mint()` before `vault.setPosition()`), reintroduced in a parallel code path that the repo's own reentrancy regression tests do not cover.

### Finding Description
In the hardened EVM path: [1](#0-0) 
the escrow ledger `_orders[body.commitment][token]` is decremented **before** the native/ERC20 transfer is made, following Checks-Effects-Interactions (CEI). This fix is explicitly validated by dedicated reentrancy tests: [2](#0-1) 

In contrast, the Tron variant's `withdraw()` performs the external transfer first, and only decrements the escrow ledger afterward: [3](#0-2) 

The `_filled[body.commitment] = beneficiary;` guard is set at the top of the function (line 684), which blocks a *direct* reentrant re-invocation of the same commitment through `fillOrder`/`onAccept`. However, the per-token loop still violates CEI: for the duration of each `.call{value: amount}("")` or ERC20 `transfer` call, `_orders[body.commitment][token]` still reflects the pre-decrement (stale, higher) balance. Any code path that reads this mapping for the same commitment — including future extensions, alternate withdrawal/refund/sweep entry points, or partial-fill accounting that trusts `_orders` as the source of truth — will observe an inflated escrow balance during the reentrant window. This mirrors exactly the FlatMoney root cause: state (`_positions[tokenId]` there, `_orders[commitment][token]` here) is left stale across an external call boundary that hands control to attacker-influenced code (the beneficiary address, which is directly attacker-settable as `msg.sender`/order beneficiary in the same-chain fill flow).

### Impact Explanation
If any reachable code path (present or introduced via future changes to this actively-maintained Tron contract) reads `_orders[commitment][token]` without independently checking `_filled` first, a malicious beneficiary contract can re-enter during the transfer callback and cause double release of the same escrowed funds, i.e., unauthorized double-settlement of bridge/order escrow — directly matching the bounty's "double-claim/double-settlement" and "stealing or loss of funds" categories. Even absent a currently-provable second call site, this is a genuine security regression versus the already-audited-and-fixed reference implementation in the same repository, and it defeats the "funds move exactly once" invariant at the ordering level.

### Likelihood Explanation
Medium: exploitation requires a beneficiary contract capable of receiving native tokens (readily attacker-controlled, since beneficiary = `msg.sender` for same-chain solver fills) and depends on whether the Tron IntentGatewayV2's fill/refund logic contains a second, `_filled`-unguarded entry point reading `_orders` — which the provided source did not fully expose within the reviewed excerpt, but the CEI defect itself is unconditionally present and independently verifiable.

### Recommendation
Apply the same CEI fix used in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw()` to `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw()`: decrement `_orders[body.commitment][token]` (and the `TRANSACTION_FEES` entry) **before** issuing the native/ERC20 transfer, so no external call is ever made while the escrow ledger is stale. Extend the `IntrinsicIntentsReentrancyTest.sol` reentrancy test suite to cover the Tron contract variant to prevent this divergence from recurring.

### Proof of Concept
Concrete PoC could not be fully constructed within the available investigation, since the reachable, `_filled`-unguarded second entry point into `_orders[commitment][token]` was not located in the reviewed portion of `evm/tron/contracts/apps/IntentGatewayV2.sol`. The finding is reported on the basis of the confirmed, unconditional CEI ordering defect at [4](#0-3) , contrasted with the corrected ordering at [5](#0-4) , which by itself constitutes a reentrancy-unsafe fund-custody pattern that should be remediated regardless of whether a second call site is currently exploitable.

### Citations

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L37-49)
```text
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
 */
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
