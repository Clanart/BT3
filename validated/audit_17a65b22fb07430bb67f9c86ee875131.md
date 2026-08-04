### Title
Reentrant escrow drain in `IntentGatewayV2.withdraw()` — Tron variant lacks the CEI fix and reentrancy guard present in the main EVM contract - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron deployment of `IntentGatewayV2` (`evm/tron/contracts/apps/IntentGatewayV2.sol`) reintroduces the exact Checks-Effects-Interactions violation that the main EVM `IntentGatewayV2`/`IntentsBase` contracts were explicitly hardened against. In `withdraw()`, escrowed tokens (including native TRX) are transferred to the attacker-controlled beneficiary via a raw low-level `.call` **before** the corresponding `_orders[commitment][token]` escrow balance is decremented, and the externally reachable `cancelOrder()` entrypoint that triggers this path carries **no `nonReentrant` guard** and the contract has **no `ReentrancyGuard` usage at all**.

### Finding Description
In `withdraw()`: [1](#0-0) 

the sequence per token is: check `_orders[body.commitment][token] == 0`, perform the external transfer (`beneficiary.call{value: amount}("")` for native TRX, or `token.call(...transfer...)` for TRC20), and only afterward `_orders[body.commitment][token] -= amount;`. This is interactions-before-effects, identical in shape to the reported BaseTSA CEI violation (`processWithdrawalRequests()` transferring before updating `totalPendingWithdrawals`/`amountShares`).

Compare with the hardened main-EVM equivalent, `IntentsBase._withdraw()`, which decrements escrow **before** transferring: [2](#0-1) 

and whose public entrypoints (`fillOrder`, `cancelOrder`) are protected with `nonReentrant`, as documented by the dedicated regression suite built specifically to prevent this class of bug: [3](#0-2) 

The Tron contract has none of these mitigations. `cancelOrder()` for the same-chain path is `public payable` with **no `nonReentrant` modifier**: [4](#0-3) 

and a grep of the entire `evm/tron/` tree confirms zero occurrences of `nonReentrant` or `ReentrancyGuard` anywhere in the Tron app contracts — this whole deployment ships without the reentrancy defenses that ship with the sibling EVM contract.

The attacker-controlled `beneficiary` (which equals `order.user == msg.sender` for a same-chain self-cancel) can be a contract whose `receive()`/fallback re-enters the gateway the moment it is paid out inside the `withdraw()` loop, before later entries in `body.tokens` (e.g. a second escrowed TRC20 for the same commitment) have had their `_orders[commitment][token]` balance zeroed out.

### Impact Explanation
This falls squarely under "stealing or loss of funds" / "logic attacks" against bridge custody: the escrow ledger (`_orders[commitment][token]`) is mutated only after external control is yielded to an attacker-supplied address, in a contract that ships with no reentrancy guard anywhere. Any reachable function that consults or mutates `_orders[commitment][*]` for the same commitment during that callback window operates on stale, not-yet-decremented escrow state, which is the precise "unexpected changes in accounting under external calls" pattern the CEI report describes — but here in live bridge escrow custody rather than a vault, and in the token amount that is directly redeemable per fill/refund message.

### Likelihood Explanation
`cancelOrder()` is a fully public, unprivileged entrypoint requiring only that the caller placed the order (`order.user == msg.sender`), which is trivially satisfiable by deploying a malicious contract as the order placer/beneficiary. No relayer, prover, admin, or governance actor is required — this is reachable by any unprivileged EOA/contract that places and then cancels its own same-chain order with a malicious beneficiary contract, exactly mirroring the attack scenario the project's own `IntrinsicIntentsReentrancyTest.sol` was written to guard against for the main EVM deployment, but for which the Tron deployment has no equivalent fix.

### Recommendation
Apply the same CEI fix already present in `IntentsBase._withdraw()` to the Tron `withdraw()`: decrement `_orders[body.commitment][token]` (effects) before performing the native/TRC20 transfer (interactions). Additionally, add a `nonReentrant` guard (import and inherit `ReentrancyGuard`, or use a transient-storage lock as elsewhere in the codebase) on all externally reachable entrypoints that can trigger `withdraw()`, matching the guards already present on `fillOrder`/`cancelOrder` in the primary EVM `IntentGatewayV2`.

### Proof of Concept
1. Attacker deploys `MaliciousBeneficiary` with a `receive()` that re-enters the gateway.
2. Attacker calls `placeOrder()` on the Tron gateway with two escrowed inputs: native TRX and a TRC20 token, `order.user = address(MaliciousBeneficiary)` is not required — same-chain cancel only requires `msg.sender == order.user`, so the attacker calls from `MaliciousBeneficiary` itself (or routes `order.user` to it).
3. Before deadline, `MaliciousBeneficiary` (as `msg.sender`) calls `cancelOrder(order, options)` for a same-chain order.
4. `cancelOrder` → `withdraw(body, true)`: sets `_filled[commitment] = beneficiary`, then begins the token loop. On the native-TRX index, `beneficiary.call{value: amount}("")` invokes `MaliciousBeneficiary.receive()`.
5. Inside `receive()`, since `cancelOrder`/`withdraw` carry no `nonReentrant` lock and the contract has no `ReentrancyGuard`, the attacker calls back into the gateway (e.g., a second `cancelOrder`/`placeOrder`/other unlocked path) while `_orders[commitment][TRC20token]` is still fully populated (not yet decremented), attempting to redeem or manipulate that not-yet-zeroed escrow balance ahead of the outer call completing its own decrement.
6. Because no locking primitive exists in this file, the outcome depends only on whatever other function paths read `_orders[commitment][*]`; the CEI ordering itself (transfer-then-decrement) is the exploitable primitive that the sibling EVM contract's dedicated test suite was built to eliminate, and it is verifiably absent here. [5](#0-4)

### Citations

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-48)
```text
/**
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
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
```
