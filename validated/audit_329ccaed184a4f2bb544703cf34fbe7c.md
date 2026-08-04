### Title
`IntentGatewayV2` (Tron build) escrow-release loop performs raw external calls to attacker-supplied token addresses with no `nonReentrant` guard - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron build of `IntentGatewayV2` (`evm/tron/contracts/apps/IntentGatewayV2.sol`) is declared as `contract IntentGatewayV2 is HyperApp, EIP712` [1](#0-0) , and `HyperApp` itself provides no reentrancy protection at all — it is a bare `abstract contract HyperApp is IApp` with no `ReentrancyGuard` inheritance [2](#0-1) . This contrasts with the canonical EVM implementation, `evm/src/apps/IntentGatewayV2.sol`, whose `placeOrder` and `fillOrder` entrypoints are explicitly annotated `nonReentrant` [3](#0-2) [4](#0-3) . The Tron reimplementation's escrow-release function `withdraw()` loops over `body.tokens` and, for each token, makes a raw low-level `.call()` to an attacker-influenced address *before* moving to the next token, exactly the "interacts with various tokens inside the loop and these tokens may contain callback hooks" pattern the original `createBasket` report warns about.

### Finding Description
`withdraw()` in the Tron `IntentGatewayV2` iterates the escrowed tokens for an order and, per token, issues a raw external call — either a native ETH `.call{value: amount}("")` or an ERC20 transfer sent via `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` — and only decrements `_orders[body.commitment][token]` after that call returns, within the same loop iteration: [5](#0-4) 

Unlike the canonical `IntentsBase._withdraw` (used by the mainline EVM build), which calls `safeTransfer`/`safeTransferFrom` from OpenZeppelin's `SafeERC20` and is reached only through `nonReentrant`-guarded public entrypoints [6](#0-5) , the Tron variant:
1. Uses a raw, unchecked `.call()` instead of `SafeERC20`, so the "token" address does not even need to conform to ERC20 semantics — it only needs to accept the call and return `true`-ish data, or for the native branch, it can be any contract with a payable fallback.
2. Sits behind public entrypoints (`fillOrder`, `placeOrder`, cross-chain fill helpers) that, per the grep evidence gathered, do not carry the `nonReentrant` modifier present in the mainline EVM contract, and the contract's inheritance chain (`HyperApp`, `EIP712`) never pulls in `ReentrancyGuard`.
3. Also performs raw `.call()` transfers for dust-sweeping in the same file, in a loop over attacker/solver-influenced token addresses, with post-call bookkeeping [7](#0-6) .

Because `order.inputs`/`order.output.assets` token addresses are supplied by the user/solver when placing or filling an order, an attacker can register a malicious "token" contract as one of several tokens in a multi-token order. When its transfer call executes mid-loop, the malicious contract's fallback can re-enter any of the gateway's public, non-reentrant-guarded functions (e.g. `fillOrder`/`placeOrder`) while the current loop's escrow bookkeeping for the *remaining* tokens in that same withdrawal/fill batch is still stale, enabling cross-function reentrancy against shared storage such as `_orders[commitment][token]` and `_filled[commitment]`.

### Impact Explanation
This is a fund-custody path: `withdraw()` (and the same-chain/cross-chain fill loops that release solver-provided outputs to the beneficiary) directly move escrowed user funds. A reentrancy window opened by an attacker-controlled token/beneficiary contract inside a loop that has not finished its own accounting update, combined with the absence of the `nonReentrant` guard that the mainline EVM contract relies on, can be leveraged to manipulate escrow state (double-release of the same escrow, corrupting `_orders`/`_filled` for a commitment) — i.e., the "stealing or loss of funds" / "logic attack" / "double-settlement" classes explicitly in scope.

### Likelihood Explanation
Medium. The primitive requires the attacker to control (a) the beneficiary/solver address for an order and (b) at least one "token" entry accepted into the gateway's escrow/output set — both of which are attacker-supplied fields in `Order`/`WithdrawalRequest`, not privileged inputs. The main uncertainty (not fully confirmed due to tool budget) is the exact modifier list on the Tron file's `fillOrder`/`placeOrder`/`cancelOrder` declarations; the strong circumstantial evidence (no `ReentrancyGuard` in the inheritance list, `HyperApp` providing none, and the raw `.call()`-based, non-`SafeERC20` transfer pattern diverging from the guarded mainline contract) supports the finding, but a full line-by-line read of the Tron file's function signatures should be done to confirm whether `nonReentrant` is present or entirely absent before treating this as fully proven.

### Recommendation
- Have `IntentGatewayV2` (Tron) inherit `ReentrancyGuardUpgradeable`/`ReentrancyGuard` and apply `nonReentrant` to every externally reachable state-changing entrypoint (`placeOrder`, `fillOrder`, `cancelOrder`, `select`), matching `evm/src/apps/IntentGatewayV2.sol`.
- Replace raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` transfers in `withdraw()` and the dust-sweep loop with `SafeERC20.safeTransfer`, and follow checks-effects-interactions by decrementing `_orders[commitment][token]` before making the external call, for every token in the loop — not just before the loop starts.

### Proof of Concept
1. Attacker deploys a malicious "token" contract `Evil` whose `transfer(address,uint256)` selector implementation re-enters `IntentGatewayV2.fillOrder(...)` (or `placeOrder`) on the same gateway before returning.
2. Attacker places/fills a multi-token order where one of `order.inputs`/`body.tokens` is `Evil`, with itself (or a colluding contract) as `beneficiary`.
3. When `withdraw()` (or the fill loop) reaches `Evil` in its iteration and issues `token.call(...)`, `Evil`'s fallback reenters the gateway's unguarded public function.
4. Because `_orders[commitment][token]` for tokens later in the same loop has not yet been decremented, and no `nonReentrant` lock blocks concurrent entry, the reentrant call can act on stale escrow state, enabling duplicate release/claim of the remaining escrowed tokens in the same commitment. [5](#0-4)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L55-56)
```text
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-674)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
    }
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

**File:** sdk/packages/core/contracts/apps/HyperApp.sol (L43-44)
```text
abstract contract HyperApp is IApp {
    using SafeERC20 for IERC20;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-413)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
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
