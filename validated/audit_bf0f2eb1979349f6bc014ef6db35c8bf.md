## Analysis

The external report's core defect — an upgradable contract that quietly changes behavior over the user's already-escrowed assets, without the escrow-holding code independently binding *who* is allowed to author that change — has a direct local analog in `IntentGatewayV2`'s cross-chain governance-message handling.

`IntentGatewayV2` is itself the escrow: `_orders` holds user input tokens and fees until fill/refund/redeem [1](#0-0) , and the contract sits behind an ERC-1967 proxy that can be repointed to a new implementation via a `RequestKind.UpgradeContract` message delivered through `onAccept` [2](#0-1) .

The dispatcher-side documentation/pallet code shows that an ISMP `PostRequest` carries two identity fields: `source` (the origin **chain**) and `from` (the origin **module/pallet** on that chain) [3](#0-2) . The intents-coprocessor pallet dispatches governance actions (including upgrades) by setting `from: PALLET_INTENTS_ID.to_vec()` [4](#0-3)  — i.e. `from` is the mechanism meant to prove the message actually originated from the governance pallet, not just from "the Hyperbridge chain" in general.

But `onAccept` for the four privileged `RequestKind`s (`NewDeployment`, `UpdateParams`, `SweepDust`, `UpgradeContract`) only checks the **chain-level** `source` field, never `from`:

```solidity
// only hyperbridge is permitted to perform these actions
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
if (kind == RequestKind.NewDeployment) { ... }
else if (kind == RequestKind.UpdateParams) { ... }
else if (kind == RequestKind.SweepDust) { ... }
else if (kind == RequestKind.UpgradeContract) {
    (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
    ERC1967Utils.upgradeToAndCall(newImpl, initData);
}
``` [5](#0-4) 

This is architecturally inconsistent with the contract's *own* pattern for `RedeemEscrow`/`RefundEscrow`, which additionally calls `_authenticate(incoming.request)` to bind the message to a specific registered peer module before touching escrow [6](#0-5) . The privileged governance branch drops that module-binding check entirely and trusts any message merely tagged with the Hyperbridge chain's `source` identifier — regardless of which pallet/account on that chain actually dispatched it.

Confirmed test coverage validates only the negative case where `source` itself is wrong (a registered peer gateway, not Hyperbridge) [7](#0-6) ; there is no test asserting that `from` must equal the intents-coprocessor pallet ID specifically, because the contract never checks it.

**What I could not verify from the index:** whether any currently-deployed, unprivileged-accessible pallet on the Hyperbridge/Nexus runtime lets an ordinary signed account dispatch an ISMP POST with an attacker-chosen `to` (targeting an EVM `IntentGatewayV2` instance) and attacker-chosen `body` (encoding `UpgradeContract`/`SweepDust`/etc.). The only generic-dispatch example I found (`modules/pallets/demo`) hardcodes its body to `"Hello from polkadot"`, so it is not itself exploitable, and I could not confirm within the index whether it is even wired into the production `nexus`/`gargantua` runtimes, or whether some other pallet exposes a similarly generic, user-parameterized dispatch path. Given the index-size limits on this codebase, I recommend starting a full Devin session to enumerate the parachain runtimes' constructed pallet sets and confirm/rule out a reachable unprivileged dispatch primitive before treating this as exploit-ready.

### Title
Governance-only `onAccept` branches in IntentGatewayV2 authenticate by source chain only, never binding the sending module/pallet - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`onAccept`'s privileged branches (`NewDeployment`, `UpdateParams`, `SweepDust`, `UpgradeContract` — including proxy implementation upgrades over the escrow-holding gateway) authenticate solely on `incoming.request.source` (the origin chain id), never on `incoming.request.from` (the specific pallet/module that dispatched the message). This diverges from the `RedeemEscrow`/`RefundEscrow` paths, which additionally call `_authenticate()` to bind the message to a registered peer module.

### Finding Description
`ExtrinsicIntents.onAccept` gates governance actions with a single check:
```solidity
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
``` [8](#0-7) 
This only proves the message came from *some* pallet on the chain identified as "hyperbridge" — it does not verify the `from` field matches the intents-coprocessor pallet ID that the governance dispatcher actually sets [9](#0-8) . Any other pallet capable of dispatching an ISMP POST from that same chain, with an attacker-influenced `to`/`body`, would pass this check and reach `ERC1967Utils.upgradeToAndCall`, `_updateParams`, `_sweepDust`, or `_addDeployment` — all of which act directly on the escrow-holding proxy and its `_orders`/`_instances` state [10](#0-9) .

### Impact Explanation
If reachable by an unprivileged dispatch path, this allows unauthorized execution of the most sensitive gateway operations: swapping the implementation behind the proxy that custodies all escrowed user funds (`UpgradeContract`), redirecting escrow to attacker-controlled fee/param settings (`UpdateParams`), sweeping accumulated dust to an attacker (`SweepDust`), or registering a rogue remote gateway instance that `_authenticate()` will subsequently trust for `RedeemEscrow`/`RefundEscrow` (`NewDeployment`) — the latter directly enabling false acceptance of withdrawal requests against real user escrow.

### Likelihood Explanation
Confirmed at the code level: the check is provably missing relative to the contract's own authentication pattern used elsewhere in the same file. Not confirmed: whether any currently-wired, user-accessible pallet in the production Hyperbridge/Nexus runtime can dispatch a POST with attacker-controlled `to`/`body` to complete the attack chain end-to-end. This second half requires further investigation beyond the indexed code available here.

### Recommendation
In the governance branch of `onAccept`, additionally verify `incoming.request.from` equals the expected intents-coprocessor pallet identifier (mirroring `_authenticate()`'s peer-binding used for `RedeemEscrow`/`RefundEscrow`), rather than trusting `source` (chain id) alone.

### Proof of Concept
Not constructible from indexed code alone — the exploit chain depends on a runtime dispatch primitive I could not locate/rule out in the current index (see "What I could not verify" above). The authentication gap itself is directly demonstrated by comparing: [5](#0-4) 
against the peer-bound path: [6](#0-5) 

Given the incomplete confirmation of a reachable unprivileged trigger, treat this as a code-level authorization gap requiring further runtime enumeration (recommend a full Devin session with filesystem access to grep all `construct_runtime!` pallet lists and any pallet exposing user-parameterized `DispatchPost` calls) rather than a fully proven exploit.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L112-116)
```text
        /**
         * @dev Upgrade the gateway implementation behind its ERC-1967 proxy.
         */
        UpgradeContract
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L521-597)
```text
    function _addDeployment(Deployment memory body) internal {
        _instances[keccak256(body.chain)] = body.gateway;
        emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
    }

    /**
     * @dev Validates gateway configuration parameters. Reverts with InvalidInput if any
     * value would brick the gateway or cause arithmetic errors in fee calculations.
     *
     * @param p The parameters to validate.
     */
    function _validateParams(Params memory p) internal view {
        if (p.host == address(0) || p.host.code.length == 0) revert InvalidInput();
        if (p.dispatcher == address(0) || p.dispatcher.code.length == 0) revert InvalidInput();
        if (p.surplusShareBps > 10_000) revert InvalidInput();
        if (p.protocolFeeBps >= 10_000) revert InvalidInput();
        if (p.priceOracle != address(0) && p.priceOracle.code.length == 0) revert InvalidInput();
    }

    /**
     * @dev Updates the gateway's configuration parameters and per-destination protocol fees.
     * Called by Hyperbridge governance to modify fee settings, host address, dispatcher,
     * price oracle, and other operational parameters.
     *
     * Validates all params before applying. Emits ParamsUpdated with the old and new params,
     * then iterates over any destination-specific fee overrides and applies them to
     * `_destinationProtocolFees`.
     *
     * @param update The parameter update containing new params and destination fee overrides.
     */
    function _updateParams(ParamsUpdate memory update) internal {
        _validateParams(update.params);

        emit ParamsUpdated({previous: _params, current: update.params});
        _params = update.params;

        for (uint256 i; i < update.destinationFees.length;) {
            bytes memory chain = update.destinationFees[i].chain;
            uint256 feeBps = update.destinationFees[i].destinationFeeBps;
            if (feeBps >= 10_000) revert InvalidInput();
            _destinationProtocolFees[keccak256(chain)] = feeBps;

            unchecked {
                ++i;
            }
            emit DestinationProtocolFeeUpdated(string(chain), feeBps);
        }
    }

    /**
     * @dev Transfers accumulated protocol dust (surplus tokens) to a specified beneficiary.
     * Called by Hyperbridge governance to sweep protocol-owned tokens that have accumulated
     * from fees, surplus splits, and calldata execution residuals.
     *
     * Supports both native tokens and ERC-20 tokens.
     *
     * @param req The sweep request containing the beneficiary address and token amounts.
     */
    function _sweepDust(SweepDust memory req) internal {
        uint256 outputsLen = req.outputs.length;
        for (uint256 i; i < outputsLen;) {
            TokenInfo memory info = req.outputs[i];
            address token = address(uint160(uint256(info.token)));
            uint256 amount = info.amount;

            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
            }
            unchecked {
                ++i;
            }
            emit DustSwept(token, amount, req.beneficiary);
        }
    }
```

**File:** docs/content/protocol/ismp/dispatcher.mdx (L10-23)
```text
```rust showLineNumbers
/// Simplified POST request, intended to be used for sending outgoing requests
pub struct DispatchPost {
    /// The destination state machine of this request.
    pub dest: StateMachine,
    /// Module identifier of the sending module
    pub from: Vec<u8>,
    /// Module identifier of the receiving module
    pub to: Vec<u8>,
    /// Relative from the current timestamp at which this request expires in seconds.
    pub timeout: u64,
    /// Encoded request body
    pub body: Vec<u8>,
}
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L924-936)
```rust
		/// Dispatch a cross-chain message to a gateway contract
		fn dispatch(state_machine: StateMachine, to: H160, body: Vec<u8>) -> DispatchResult {
			// Create dispatcher instance
			let dispatcher = T::Dispatcher::default();

			// Create ISMP post request
			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_INTENTS_ID.to_vec(),
				to: to.0.to_vec(),
				timeout: 0, // No timeout for governance actions
				body,
			};
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-309)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3817-3829)
```text
    function testOnAcceptUpgradeContractRejectsNonHyperbridgeSource() public {
        address implBefore = _implementationOf(address(intentGateway));
        IntentGatewayV2Upgraded newImpl = new IntentGatewayV2Upgraded(address(this));

        // A registered peer gateway (not the Hyperbridge coprocessor) must not be able to upgrade.
        PostRequest memory request = _upgradeRequest(bytes("SOURCE_CHAIN"), address(newImpl), "");

        vm.prank(address(host));
        vm.expectRevert(IntentsBase.Unauthorized.selector);
        intentGateway.onAccept(IncomingPostRequest({relayer: address(0), request: request}));

        assertEq(_implementationOf(address(intentGateway)), implBefore, "implementation must be unchanged");
    }
```
