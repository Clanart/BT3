### Title
Permissionless registration of arbitrary ERC20 contracts as token pairs enables self-referential balance checks and unbacked minting of native Cosmos coins - (File: x/erc20/keeper/msg_server.go)

### Summary
The external report describes `SwapFacade` accepting any user-supplied, unauthorized `SwapExecutor` address, letting an attacker deploy a malicious executor that bypasses fee logic. The Cosmos EVM analog is `x/erc20`'s `MsgRegisterERC20` handler, which — when `PermissionlessRegistration` is enabled — allows **any account to register any arbitrary hex address as an ERC20 "token pair" backing a native Cosmos coin**, with no vetting of the contract's bytecode/behavior [1](#0-0) . Just as the argon `SwapFacade` trusted an attacker-chosen executor to honestly account fees, the `ConvertERC20`/`ConvertCoin` flow trusts an attacker-chosen ERC20 contract to honestly report its own `balanceOf`/`transfer` results.

### Finding Description
`RegisterERC20` permits permissionless registration of any `common.IsHexAddress` value as an ERC20 contract token pair when governance has set `PermissionlessRegistration = true`: [1](#0-0) 

The subsequent conversion logic in `convertERC20IntoCoinsForNativeToken` determines how many native coins to mint by calling `BalanceOf` on the **same attacker-controlled contract** both before and after invoking `transfer` on that contract, then trusting the delta to decide how many coins to mint: [2](#0-1) 

```
balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
...
res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
...
balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
...
if r := balanceTokenAfter.Cmp(expToken); r != 0 { return error }
// Mint coins
if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil { ... }
k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins)
```

Because the attacker fully controls the deployed "ERC20" contract's code, they can implement `balanceOf()` and `transfer()` to unconditionally satisfy these self-referential invariant checks (e.g., always report the expected post-transfer balance, or always return `true`) without any real value ever being locked. The invariant check is not a genuine escrow verification — it queries the very contract the attacker wrote, so it provides no security guarantee. This lets the attacker call `ConvertERC20` repeatedly to have `k.bankKeeper.MintCoins` mint arbitrary amounts of a native Cosmos coin denomination that is entirely unbacked.

### Impact Explanation
This matches the "Critical unauthorized minting ... of spendable user value across native balances" impact category. Once minted, the attacker holds genuine native `sdk.Coin` balances denominated as a real token-pair denom (`pair.Denom`), which is fungible, transferable via `x/bank`, usable in the fee market, transferable over IBC, and convertible through legitimate protocol rails — this is not merely a fake ERC20 balance confined to EVM state; it is real, spendable value created out of nothing, corrupting the chain's total-supply/accounting invariants.

### Likelihood Explanation
Exploitability depends entirely on the `PermissionlessRegistration` governance parameter (default value could not be fully confirmed via inspection due to indexing limits on `x/erc20/types/params.go`, though the field exists and is checked at `x/erc20/keeper/msg_server.go:331`). If this parameter is enabled on a deployed chain (many EVM-compatible Cosmos chains do enable permissionless ERC20 registration to bootstrap DeFi/liquidity), any unprivileged EVM user can trivially deploy a malicious ERC20 contract and exploit this with a single `MsgRegisterERC20` + repeated `MsgConvertERC20` calls — no privileged access required.

### Recommendation
- Do not rely on self-reported `balanceOf`/`transfer` return values from an unvetted, permissionlessly-registered contract as the sole invariant for minting native coins.
- Restrict `ConvertERC20`'s trust assumptions: either require registered ERC20s under `PermissionlessRegistration` to be validated against a known-safe bytecode template (similar to how `RegisterERC20Extension`/dynamic precompiles use a fixed, audited `Erc20Bytecode`), or disallow arbitrary externally-owned-account-deployed bytecode from being registered as a mintable-backing token pair.
- Consider decoupling "permissionless registration for EVM display purposes" from "eligibility to mint native coins," so a malicious contract can, at most, misrepresent its own EVM-visible balance without being able to trigger `bankKeeper.MintCoins`.
- Add supply-cap/rate-limiting or a governance-gated allowlist analogous to the argon report's recommendation of "a list of allowed executors," here applied to which ERC20 contracts are eligible for coin-minting conversion.

### Proof of Concept
1. Governance/chain config has `x/erc20` `Params.PermissionlessRegistration = true` (attacker does not need to control this — only needs it to already be enabled).
2. Attacker deploys a malicious ERC20-like contract `Evil` where:
   - `transfer(to, amount)` always returns `true` and emits a `Transfer` event, but does not actually decrement the caller's balance.
   - `balanceOf(module_address)` returns a value that increases by exactly `amount` on each call following a `transfer`, regardless of whether tokens were truly received (e.g., by tracking a counter unrelated to real token movement).
3. Attacker submits `MsgRegisterERC20{Erc20Addresses: [Evil]}` — this succeeds permissionlessly per `x/erc20/keeper/msg_server.go:326-350`, creating a token pair and native denom backed by `Evil`.
4. Attacker submits `MsgConvertERC20{ContractAddress: Evil, Amount: X, Receiver: attacker}`.
5. `convertERC20IntoCoinsForNativeToken` calls `Evil.transfer(ModuleAddress, X)`, checks `Evil.balanceOf(ModuleAddress)` before/after (both controlled by the attacker's contract to match `expToken`), and proceeds to `k.bankKeeper.MintCoins` and send `X` native coins to the attacker's receiver address — with zero real tokens ever escrowed.
6. Attacker repeats step 4 to mint unlimited native coins of that denom, which are real, spendable, transferable `x/bank` balances.

### Citations

**File:** x/erc20/keeper/msg_server.go (L78-130)
```go
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()
	balanceCoin := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceToken == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-350)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}
```
