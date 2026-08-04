## Analysis

`CallDispatcher.dispatch()` is the local analog to the reported `Account.sol` issue: a contract whose sensitive execution primitive is meant to be invoked only by a trusted caller in a specific flow, but is left reachable directly by anyone because there's no explicit caller-binding check. [1](#0-0) 

`CallDispatcher` is deployed once per chain/environment and shared across `IntentGatewayV2`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` (same `CALL_DISPATCHER` address configured for all of them): [2](#0-1) [3](#0-2) [4](#0-3) 

Its `dispatch(bytes memory encoded)` function is `external` with **no access control whatsoever** — no `onlyOwner`, no caller allowlist, nothing that restricts it to the gateway/token contracts that are supposed to be the only legitimate invokers: [5](#0-4) 

The intended flow temporarily parks funds in `CallDispatcher` and then sweeps back only the tokens it explicitly expects. For example, in `IntentsBase._execute`, the sweep loop only recovers balances for `order.output.assets[i].token` — any other token balance left on the dispatcher (e.g., an intermediate/byproduct token from a DEX swap invoked via `order.output.call`, or unspent input remnants from `_predispatch`) is never accounted for and stays on the contract indefinitely: [6](#0-5) 

Because `dispatch()` has no caller restriction, **any unprivileged address** can call it directly with an arbitrary `Call[]` targeting that residual balance (e.g. `Call({to: leftoverToken, value: 0, data: transfer(attacker, balance)})`), draining whatever the shared `CallDispatcher` happens to be holding — dust, timing-window balances, or byproduct tokens from any of the apps that route through it (`IntentGatewayV2` predispatch/postdispatch, `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata execution).

### Title
Unauthenticated `CallDispatcher.dispatch()` allows anyone to drain residual/dust balances held by the shared dispatcher - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` is a public, unauthenticated entry point that executes arbitrary `Call[]` from the dispatcher's own balance. The contract is a shared singleton used by `IntentGatewayV2`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` to route predispatch/postdispatch calldata, and its sweep logic in each caller only recovers tokens it explicitly expects (`order.inputs`/`order.output.assets`). Any token or native balance left on the dispatcher outside of those tracked assets — e.g., byproduct tokens from a swap executed via `order.output.call`/`order.predispatch.call`, or funds that land there between steps of a multi-call flow — can be swept by any external, unprivileged address by simply calling `dispatch()` directly, since the function performs no caller check at all.

### Finding Description
`dispatch()` decodes an arbitrary `Call[]` and executes each call from the dispatcher's own context with the dispatcher's own balance (`to.call{value: call.value}(call.data)`), with the only checks being "target has code" and "call succeeded". There is no restriction limiting who may call `dispatch()` — it is not scoped to the gateway contracts (`IntentGatewayV2`, `HyperFungibleToken`, `WrappedHyperFungibleToken`) that are the intended, exclusive users of this function. Because the dispatcher is deployed once and shared across all of these apps, any balance sitting on it — even momentarily, or as permanently un-swept dust from an output/predispatch call that produces a token not tracked by the caller's sweep loop — is a bounty for whoever calls `dispatch()` first.

### Impact Explanation
This directly causes loss of funds: value that legitimately belongs to users/protocol (dust from swaps, byproduct tokens, or transiently held escrow assets) can be stolen by any unprivileged attacker with a single unauthenticated call to a production contract shared by multiple live apps. This matches the bounty's "stealing or loss of funds" and "unauthorized execution" categories, and requires no privileged role, malicious relayer, or governance actor — an attacker only needs to observe a non-zero balance on the public `CallDispatcher` address and race to call `dispatch()`.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: the dispatcher's balance is publicly observable on-chain at all times (any ERC20 `balanceOf`/native balance check), and any leftover balance (from calldata-driven swaps producing an untracked token, from any of the three integrated apps) is a standing, permanent target since nothing but the initial legitimate caller ever tries to sweep it, and that caller only sweeps tokens it explicitly enumerates.

### Recommendation
Add a caller restriction to `CallDispatcher.dispatch()` (e.g., an allowlist of the deployer-registered gateway/token contracts, or an `onlyAuthorizedCaller` modifier set at deployment), so the function can only be invoked by the specific contracts that are meant to route calls through it — mirroring the report's core recommendation of explicitly binding a sensitive execution primitive to its intended caller/context rather than leaving it openly reachable.

### Proof of Concept
1. `IntentGatewayV2.fillOrder()` (or `placeOrder()`) executes `order.output.call` (or `predispatch.call`) via `ICallDispatcher(dispatcher).dispatch(order.output.call)`, where the encoded calls swap an output token through a DEX for some other token `X` not present in `order.output.assets`. [7](#0-6) 
2. The sweep loop in `_execute` only checks/sweeps `order.output.assets[i].token` balances, so the resulting `X` balance is left sitting on the shared `CallDispatcher` contract. [8](#0-7) 
3. An attacker observes `X.balanceOf(callDispatcherAddress) > 0` and calls `CallDispatcher.dispatch(abi.encode([Call({to: X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly — no permission check in `dispatch()` prevents this. [5](#0-4) 
4. The attacker receives the stranded token balance, which rightfully belonged to the protocol/user flow that generated it.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L25-62)
```text
contract CallDispatcher is ICallDispatcher {
    /**
     * @dev error thrown when the target is not a contract.
     */
    error NotContract(address target);

    /**
     * @dev error thrown when a call fails.
     */
    error CallFailed(address target, bytes result);

    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
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

**File:** evm/script/DeployIsmp.s.sol (L146-163)
```text
        // ============= Deploy applications =============
        CallDispatcher callDispatcher = new CallDispatcher{salt: salt}();
        BandwidthManager bandwidthManager = new BandwidthManager{salt: salt}(admin);
        bandwidthManager.setHost(address(host));
        
        vm.stopBroadcast();

        // ============= Write addresses to config =============
        if (!isMainnet) {
            config.set("TOKEN_FAUCET", address(faucet));
            config.set("FEE_TOKEN", feeToken);
        }
        config.set("HOST", address(host));
        config.set("ECDSA_BEEFY", address(ecdsaBeefy));
        config.set("SP1_BEEFY", address(sp1Beefy));
        config.set("HANDLER_V2", address(handler));
        config.set("CONSENSUS_ROUTER", address(consensusRouter));
        config.set("CALL_DISPATCHER", address(callDispatcher));
```

**File:** evm/script/DeployHFT.s.sol (L14-20)
```text
        CallDispatcher dispatcher = new CallDispatcher{salt: salt}();
        HyperFungibleToken hft = new HyperFungibleToken{salt: salt}(name, symbol, admin);

        hft.configure(HyperFungibleToken.ConfigOptions({
            host: HOST_ADDRESS,
            dispatcher: address(dispatcher)
        }));
```

**File:** evm/script/DeployWrappedHFT.s.sol (L14-22)
```text
        CallDispatcher dispatcher = new CallDispatcher{salt: salt}();
        WrappedHyperFungibleToken whft = new WrappedHyperFungibleToken{salt: salt}(admin);

        whft.configure(WrappedHyperFungibleToken.WrappedConfigOptions({
            host: HOST_ADDRESS,
            dispatcher: address(dispatcher),
            underlying: underlying,
            isWeth: isWeth
        }));
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-474)
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

```
