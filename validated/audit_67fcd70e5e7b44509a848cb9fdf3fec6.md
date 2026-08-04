[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-639)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
        } else if (kind == RequestKind.UpdateParams) {
            // Decode the body which includes optional destination-specific protocol fee updates
            ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
            emit ParamsUpdated({previous: _params, current: update.params});
            _params = update.params;
```

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L582-592)
```text
    function testMarkupCapEnforced() public {
        SimplexPaymasterHarness implementation = new SimplexPaymasterHarness();
        (SimplexPaymaster.Params memory params, address[] memory tokens, AggregatorV3Interface[] memory oracles) =
            _initArgs(5_001);
        bytes memory initData = abi.encodeCall(
            SimplexPaymaster.initialize,
            (address(hyperbridgeHost), params, tokens, oracles)
        );
        vm.expectRevert(abi.encodeWithSelector(SimplexPaymaster.InvalidMarkup.selector, uint256(5_001)));
        new ERC1967Proxy(address(implementation), initData);
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L309-317)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

```
