## Analysis

The Teller report describes lenders losing funds because their protocol assumes `transfer`/`transferFrom` always moves the exact requested `amount`, when in fact a fee-on-transfer ERC20 can deliver less than that to the recipient. The equivalent bug class exists in sei-chain's CW20→ERC20 pointer wrapper, which is used to expose arbitrary, permissionlessly-registered CosmWasm CW20 tokens as ERC20 tokens to the EVM (and to any downstream EVM DeFi contract, e.g. AMM pools deployed against pointer tokens).

### Title
Fee/behavior-altering CW20 tokens wrapped by `CW20ERC20Pointer` break the ERC20 "amount == amount received" invariant relied on by downstream EVM contracts - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer.transfer()` and `transferFrom()` blindly forward the caller-specified `amount` to the underlying CW20 contract's `transfer`/`transfer_from` message and return `true` on success, without verifying that the recipient's actual balance increased by exactly `amount`. Any Sei address can permissionlessly register a pointer for an arbitrary CW20 contract via the Pointer precompile, so a CW20 token that charges a transfer fee, tax, or otherwise delivers less than the nominal amount (a legal extension of the CW20 spec, analogous to fee-on-transfer ERC20s) will cause the ERC20 pointer to misreport the actual value moved to any EVM contract that composes with it.

### Finding Description
`CW20ERC20Pointer.transfer` and `transferFrom` construct a CosmWasm `transfer`/`transfer_from` message with the literal `amount` and execute it via the wasmd precompile, then unconditionally return `true`: [1](#0-0) 

There is no balance-delta check comparing the recipient's balance before and after the CosmWasm call, unlike the fee-on-transfer-aware swap functions in the Uniswap router bundled in this repo, which explicitly measure `balanceOf(to)` before/after to account for tokens that don't deliver the full nominal amount: [2](#0-1) 

Registration of a pointer for an arbitrary CW20 contract is permissionless — any caller can invoke the Pointer precompile's `addCW20Pointer`, which queries `token_info` from the target CW20 contract and deploys a `CW20ERC20Pointer` without any check on the CW20 contract's transfer semantics: [3](#0-2) [4](#0-3) 

Because CW20 is only a message-interface convention (not an enforced accounting standard), nothing prevents a CW20 contract from implementing a transfer tax, burn-on-transfer, or rebase that delivers less than `amount` to the recipient. Downstream EVM contracts that build on top of the pointer token (AMM pools, lending markets, vaults) and follow the common ERC20 assumption "if `transfer`/`transferFrom` returns `true`, the recipient's balance increased by exactly `amount`" will silently under-collateralize, mis-price, or lose funds — exactly the same failure mode as the original Teller report, just relocated to the pointer/EVM bridge boundary instead of a native ERC20.

### Impact Explanation
Any EVM contract (AMM pair, lending pool, escrow) built using a `CW20ERC20Pointer` token as collateral or a swap asset can be tricked into accounting for more tokens than it actually received, if the underlying CW20 implements any fee/tax/burn-style transfer. This leads to fund loss for counterparties (lenders/LPs) in a manner directly analogous to the referenced report, and is a permanent, exploitable value leak once such a CW20 is registered and integrated.

### Likelihood Explanation
Registration of CW20 pointers is fully permissionless (`addCW20Pointer` precompile call, reachable by any Sei/EVM address), and CW20 contracts with custom transfer logic (taxes, burns, hooks) are a well-known pattern in the Cosmos ecosystem. An attacker only needs to deploy such a CW20 token, register the pointer, and get it adopted by (or seed) an EVM pool/lending market to realize the loss.

### Recommendation
In `CW20ERC20Pointer.transfer` and `transferFrom`, query the recipient's CW20 balance before and after the `_execute` call and use the observed delta (rather than the nominal `amount`) when emitting `Transfer` events / determining success, or explicitly document and disallow pointer registration for CW20 contracts whose `transfer`/`transfer_from` do not preserve amount, mirroring the `balanceOf` delta pattern used in `swapExactTokensForTokensSupportingFeeOnTransferTokens`.

### Proof of Concept
1. Deploy a custom CW20 contract that charges a 5% fee on every `transfer`/`transfer_from` (burns or redirects the fee), fully valid under the permissive CW20 message interface.
2. Call the Pointer precompile's `addCW20Pointer` (permissionless) to deploy a `CW20ERC20Pointer` for this CW20 token — see `AddCW20` in `precompiles/pointer/pointer.go`.
3. Use the resulting ERC20 pointer as the asset in an EVM lending pool or AMM pair; deposit/repay `amount` via `pointer.transferFrom(...)`.
4. The pool's accounting records `amount` as received (since `transferFrom` returns `true`), while the pointer contract (and therefore the pool) actually only received `0.95 * amount` in the underlying CW20 balance — an immediate, uncorrected shortfall identical to the fee-on-transfer issue in the original report.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L79-96)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** integration_test/load_generator/contracts/uniswapv2/UniswapV2Router02.sol (L340-356)
```text
    function swapExactTokensForTokensSupportingFeeOnTransferTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external virtual override ensure(deadline) {
        TransferHelper.safeTransferFrom(
            path[0], msg.sender, UniswapV2Library.pairFor(factory, path[0], path[1]), amountIn
        );
        uint balanceBefore = IERC20Uniswap(path[path.length - 1]).balanceOf(to);
        _swapSupportingFeeOnTransferTokens(path, to);
        require(
            IERC20Uniswap(path[path.length - 1]).balanceOf(to).sub(balanceBefore) >= amountOutMin,
            'UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT'
        );
    }
```

**File:** precompiles/pointer/pointer.go (L134-164)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/pointer/Pointer.sol (L13-15)
```text
    function addCW20Pointer(
        string memory cwAddr
    ) external returns (address ret);
```
