## Analysis

The external report's core broken invariant is: **an opcode/function that performs a low-level `call`-family execution to an attacker-chosen address has no caller restriction, so anyone can weaponize it to move value out of the contract it executes from.**

The direct local analog is `CallDispatcher.dispatch()`.### Title
Unrestricted `CallDispatcher.dispatch()` allows anyone to drain any token/ETH balance held by the shared dispatcher - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` is a fully public `external` function with **no caller restriction whatsoever** — no `onlyHost`, no `restrict(owner)`, no allowlist of callers, and no check that the invoking transaction originates from `IntentGatewayV2`, `HyperFungibleToken`, or `WrappedHyperFungibleToken`. It simply decodes an attacker-supplied `Call[]` and executes `to.call{value: call.value}(call.data)` for each entry, using **the dispatcher contract's own balance** as the source of `value`. This is the direct analog of the HyVM `callcode` finding: an execution primitive with no access restriction that lets *anyone* force a shared, fund-holding contract to send its balance to an address of the caller's choosing. [1](#0-0) 

### Finding Description
`CallDispatcher` is a **single shared, publicly-addressed** utility contract used by multiple apps — `IntentGatewayV2` (order predispatch/output execution) and `HyperFungibleToken`/`WrappedHyperFungibleToken` (post-mint/unlock calldata execution) — to run arbitrary calls on behalf of orders/messages while temporarily holding funds. [2](#0-1) [3](#0-2) 

The contract accepts native ETH via a permissive `receive()` and holds ERC20 balances mid-flow (assets are transferred to the dispatcher, then `dispatch()` is invoked to spend/forward them, and — for `IntentGatewayV2` — a follow-up sweep call moves out only the balances that are explicitly listed in `order.inputs`/`order.output.assets`): [4](#0-3) [5](#0-4) 

Because `dispatch()` itself carries no access control, **any address that ends up with a balance sitting on the dispatcher — for any reason — can be swept out by any unrelated third party**, not just by the app that put it there:
- Tokens or ETH sent directly to `CallDispatcher`'s address by mistake (its address is documented/public, and it has a permissive `receive()`), are permanently and immediately stealable by anyone via `dispatch()`.
- Any token balance accrued during a predispatch/output execution that is **not** one of the enumerated `order.inputs`/`order.output.assets` tokens (e.g., LP-unwrap byproducts, staking/airdrop rewards, referral tokens produced by a DEX/vault call in `order.predispatch.call`/`order.output.call`) is swept only for the explicitly listed tokens — the sweep logic in `IntentsBase._execute` and the predispatch flow in `IntentGatewayV2.placeOrder` only iterate over `order.inputs`/`order.output.assets`, so any other token left on the dispatcher is unprotected dust that persists across transactions and is drainable by anyone calling `dispatch()` directly. [6](#0-5) [7](#0-6) 

This exactly mirrors the reported bug class: an unrestricted low-level execution primitive (`callcode` in HyVM / `to.call{value}(data)` in `CallDispatcher.dispatch()`) lets an unprivileged attacker direct value transfers out of a contract that is not supposed to be freely callable by arbitrary parties. Unlike `EvmHost.withdraw()` or `HostManager.onAccept()`, which are correctly gated with `restrict(...)` modifiers, `CallDispatcher.dispatch()` has none. [8](#0-7) [9](#0-8) 

### Impact Explanation
Any value that comes to rest on the `CallDispatcher` contract — whether from user/operator error (direct transfers to a documented, shared contract address) or from unenumerated token dust generated during legitimate order/message execution (swap rewards, LP-unwrap remainders, airdrops) — is permanently and trivially stealable by any unprivileged address. This is a direct loss-of-funds vector on a contract that multiple production apps (`IntentGatewayV2`, `HyperFungibleToken`, `WrappedHyperFungibleToken`) rely on and route funds through, satisfying the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" criteria.

### Likelihood Explanation
The attack requires no special privileges, no relayer/prover/admin compromise, and no malformed proofs — it is a single unauthenticated call to a public function (`dispatch(bytes)`) on a contract whose address is documented and shared across integrations. The only prerequisite is that some balance exists on the dispatcher at the time of the call, which is a realistic and recurring condition (accidental transfers to a known bridge-adjacent address, or byproduct tokens from arbitrary `predispatch.call`/`output.call` execution that fall outside the enumerated asset list).

### Recommendation
- Short term: restrict `CallDispatcher.dispatch()` to a caller allowlist (e.g., only the registered `IntentGatewayV2`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` addresses), analogous to the `restrict(...)` pattern already used in `EvmHost`/`HostManager`.
- Long term: avoid a shared, balance-holding dispatcher pattern entirely — use per-call/per-order ephemeral execution contexts (e.g., CREATE2 disposable executors) so no dispatcher-level balance can ever persist between transactions or be reachable by unrelated callers; add sweep logic that captures the dispatcher's *entire* balance (not just the enumerated order tokens) back to a trusted controller after every execution.

### Proof of Concept
1. Any user (or the `IntentGatewayV2`/HFT flow itself, via a `predispatch.call`/`output.call` that swaps into or receives an unlisted token) causes `CallDispatcher` to hold a balance of `TOKEN_X` that is **not** part of `order.inputs`/`order.output.assets` — e.g., a DEX router route paid out an extra reward token, or a user sent `TOKEN_X` directly to `CallDispatcher`'s well-known address.
2. Because `IntentsBase._execute`/`IntentGatewayV2.placeOrder` only sweep tokens explicitly enumerated in the order, `TOKEN_X` remains on the `CallDispatcher` contract after the legitimate transaction completes.
3. Attacker calls `CallDispatcher.dispatch(abi.encode([Call({to: TOKEN_X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly — this succeeds because `dispatch()` has no `msg.sender` check. [10](#0-9) 
4. `TOKEN_X`'s full balance is now transferred to the attacker, with no relationship to any order the attacker was ever party to.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-39)
```text
    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}
```

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-258)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-328)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleToken.Message memory message = abi.decode(request.body, (HyperFungibleToken.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-485)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            }

            unchecked {
                ++i;
            }
        }

        if (sweepCount > 0) {
            Call[] memory finalCalls = new Call[](sweepCount);
            for (uint256 i; i < sweepCount;) {
                finalCalls[i] = sweepCalls[i];
                unchecked {
                    ++i;
                }
            }
            ICallDispatcher(dispatcher).dispatch(abi.encode(finalCalls));
        }
    }
```

**File:** evm/src/core/EvmHost.sol (L651-660)
```text
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
    }
```

**File:** evm/src/core/HostManager.sol (L95-98)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
