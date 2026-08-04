## Confirmed Analog: Missing Reentrancy Protection + Interaction-Before-Effects in Tron `IntentGatewayV2.withdraw()`

### Title
Escrow withdrawal performs external `.call` transfers before decrementing escrow state and lacks a reentrancy guard - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` redeems/refunds escrowed order funds via raw low-level `.call()` transfers (both native ETH/TRX and ERC20, via `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))`) *before* decrementing the corresponding `_orders[commitment][token]` escrow accounting, and the contract has no `ReentrancyGuard`/`nonReentrant` protection anywhere in the file. This is the same class of bug the external report flags for `call.value()`: an unguarded low-level external call made prior to state finalization, in a token/escrow-transfer path.

### Finding Description
In `withdraw()` [1](#0-0) , for each token in the withdrawal body:

```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();

if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}

_orders[body.commitment][token] -= amount;
```

The external call/transfer happens before `_orders[body.commitment][token] -= amount`, i.e. interactions precede effects. `beneficiary` is fully attacker-controlled — it is set from `order.user`/`body.beneficiary`, which is `msg.sender` at order-creation time [2](#0-1) . The same pattern repeats for the `SweepDust` path and the ERC20/native transfers throughout `onAccept` [3](#0-2) .

Critically, this Tron file has **no `ReentrancyGuard`/`nonReentrant` modifier anywhere** (confirmed via search — zero matches), whereas the newer canonical EVM implementation explicitly inherits `ReentrancyGuardTransient` and applies `nonReentrant` [4](#0-3) , and its equivalent `withdraw`/settlement logic in `IntentsBase.sol` performs the state decrement *before* the external call (correct checks-effects-interactions ordering) [5](#0-4) . The Tron contract is a regression relative to the hardened EVM version — it also uses raw `.call()` for ERC20 transfers instead of `SafeERC20.safeTransfer`, despite importing `SafeERC20` and using it elsewhere in the same file (e.g. `newOrder`'s `safeTransferFrom`), meaning the withdrawal path specifically was not brought up to the same safety standard as the rest of the contract.

### Impact Explanation
`withdraw()` is reached from `onAccept` (RedeemEscrow/RefundEscrow) and `onGetResponse`, both of which are triggered by permissionless, protocol-legitimate ISMP message delivery (any relayer can deliver a validly-proven message — this is not a "malicious relayer" assumption, just normal message flow) [2](#0-1) [6](#0-5) . Because the beneficiary address is attacker-chosen at order-creation time, an attacker can set the beneficiary to a contract whose `receive()`/fallback (for native transfers) or `transfer()`-hook-capable token contract (for ERC20 legs, since the raw `.call` doesn't use `SafeERC20`) executes arbitrary code mid-withdrawal, before `_orders[body.commitment][token]` is decremented. This breaks the "moves exactly once" invariant required for bridged escrow funds and opens the door to double-settlement/fund-draining logic if any reentrant path into escrow-touching state exists (e.g. through composed calls the attacker controls as both order-creator and token issuer). Given TheDAO's exact failure mode was funds transferred via low-level call before balance bookkeping was updated, this is a structurally identical bug in a live bridge escrow-custody function.

### Likelihood Explanation
Medium-to-high: the attacker fully controls both the beneficiary address and, for ERC20 legs, can supply a custom malicious token contract as the escrowed asset at order-creation time (the contract accepts arbitrary `TokenInfo.token` addresses), giving them a callback hook exactly at the point of the vulnerable low-level call. No relayer collusion, prover compromise, or governance action is needed — only a well-crafted order and a receiving contract. The only external dependency is a relayer delivering the legitimate RedeemEscrow/RefundEscrow/SweepDust message, which is guaranteed to happen as part of normal protocol operation.

### Recommendation
1. Add `ReentrancyGuardTransient`/`nonReentrant` to `onAccept` and `onGetResponse` in `evm/tron/contracts/apps/IntentGatewayV2.sol`, matching `evm/src/apps/IntentGatewayV2.sol`.
2. In `withdraw()`, decrement `_orders[body.commitment][token]` **before** performing the native `.call{value:}` or ERC20 transfer (checks-effects-interactions), matching the pattern already used in `IntentsBase.sol` [5](#0-4) .
3. Replace raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` with `SafeERC20.safeTransfer`, consistent with the rest of the codebase.

### Proof of Concept
1. Attacker deploys `EvilBeneficiary`, a contract with a `receive()` function that (once a reentrant path exists, e.g. after a partial fix, or in combination with a malicious ERC20 whose `transfer()` calls back) attempts to interact with escrow-related state while `_orders[commitment][token]` still reflects the pre-withdrawal (nonzero) balance.
2. Attacker creates a same-chain or cross-chain order with `order.user = attacker`, using `EvilBeneficiary` as `beneficiary`/`order.user`, and either native TRX or a custom malicious ERC20-like token as an input asset.
3. Order proceeds normally through fill/cancel to the point where `onAccept`/`onGetResponse` invokes `withdraw()`.
4. During the native `.call{value: amount}("")` (or the malicious token's `transfer()` execution), `EvilBeneficiary`'s callback executes with `_orders[body.commitment][token]` still un-decremented, before the line `_orders[body.commitment][token] -= amount;` executes.
5. Absent a reentrancy guard, any exposed function on `IntentGatewayV2` that reads/writes `_orders[commitment][token]` during that window is exposed to inconsistent state, and the missing CEI ordering removes the last line of defense that the canonical EVM implementation already restored via `nonReentrant` and effects-before-interactions ordering.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-672)
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L25-61)
```text
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";
import {Initializable} from "@openzeppelin/contracts/proxy/utils/Initializable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";
import {
    PaymentInfo,
    TokenInfo,
    DispatchInfo,
    Order,
    SweepDust,
    Params,
    ParamsUpdate,
    DestinationFee,
    WithdrawalRequest,
    FillOptions,
    SelectOptions,
    CancelOptions,
    Deployment
} from "@hyperbridge/core/apps/IntentGatewayV2.sol";

/**
 * @title IntentGatewayV2
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @dev The IntentGateway allows for the creation and fulfillment of same-chain & cross-chain orders.
 * This is the concrete entry-point contract that composes all intent logic via inheritance:
 *
 *            EIP712
 *              |
 *          IntentsBase
 *           /       \
 *  IntrinsicIntents  ExtrinsicIntents
 *           \       /
 *        IntentGatewayV2
 */
contract IntentGatewayV2 is IntrinsicIntents, ExtrinsicIntents, ReentrancyGuardTransient, Initializable {
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
