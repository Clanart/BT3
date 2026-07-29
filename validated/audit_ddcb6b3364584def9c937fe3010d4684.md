## Analysis

The OUSD report's root cause is a strict `require(balanceAfter == balanceBefore + amount)` check placed after an ERC-20 style `transfer` call, which reverts whenever the token's transfer/balance semantics don't guarantee an exact, unrounded delta (rebase, fee-on-transfer, internal rounding). In this Cosmos EVM codebase, the direct structural analog lives in `x/erc20/keeper/msg_server.go`, where the "native ERC20" token-pair conversion flow performs the exact same pattern: transfer tokens via `CallEVM`/`CallEVMWithData`, then assert `balanceAfter == balanceBefore + amount` with `ErrBalanceInvariance`, for *arbitrary* externally-owned ERC20 contracts.

### Title
Strict Balance-Equality Invariant Check in Native ERC20 Conversion Permanently Freezes Escrowed Token-Pair Backing - (File: x/erc20/keeper/msg_server.go)

### Summary
`convertERC20IntoCoinsForNativeToken` and `ConvertCoinNativeERC20` in `x/erc20/keeper/msg_server.go` enforce a strict `Cmp`/`Equal` balance-delta invariant immediately after calling `transfer` on an arbitrary, permissionlessly-registerable native ERC20 contract [1](#0-0) . For any ERC20 whose transfer semantics do not guarantee an exact unrounded balance delta (fee-on-transfer, deflationary, or rebasing/rounding tokens — the same class of token behavior described in the OUSD report), this invariant check permanently reverts, exactly mirroring the OUSD `_withdrawCollateralUnderNFT` bug pattern.

### Finding Description
`RegisterERC20` allows any external ERC20 contract to be registered as a `TokenPair` and, when `PermissionlessRegistration` is enabled, this can be done by any unprivileged user without going through governance [2](#0-1) [3](#0-2) . Once registered, users can freely deposit (`ConvertERC20`) and withdraw (`ConvertCoin`) between the native Cosmos coin representation and the ERC20.

Both directions rely on an exact balance-delta assertion after calling the token's `transfer` function:

- `convertERC20IntoCoinsForNativeToken`: escrows tokens on the module account via `transfer`, then requires `balanceTokenAfter == balanceToken + tokens` or reverts with `ErrBalanceInvariance` [4](#0-3) .
- `ConvertCoinNativeERC20`: unescrows tokens from the module account to the receiver via `transfer`, then requires `balanceTokenAfter == balanceToken + amount` or reverts with `ErrBalanceInvariance` [5](#0-4) .

This is precisely the OUSD `_withdrawCollateralUnderNFT` require-statement pattern: an assumption that `transfer(amount)` always changes the recipient's queried balance by exactly `amount`. Any token with fee-on-transfer, deflationary burn-on-transfer, or rebase/rounding behavior on `balanceOf` violates this assumption and causes a deterministic, permanent revert on every subsequent call for that token pair.

The critical difference from a simple DoS is that `ConvertCoinNativeERC20` is also invoked from the IBC receive callback path for native ERC20 token pairs (`OnRecvPacket` → `ConvertCoinNativeERC20`) [6](#0-5) , and Coins for such a pair can already be in circulation (minted via a prior successful `ConvertERC20` deposit, or received via IBC) while the underlying ERC20 tokens sit escrowed under `types.ModuleAddress`. If the registered ERC20's `transfer`/`balanceOf` semantics do not preserve an exact 1:1 delta — which is a legitimate transfer-token property, not solely a "malicious owner" behavior — then any holder of the Cosmos-native coin representation can never redeem it back into the ERC20 through `ConvertCoinNativeERC20`, because the balance-equality check will always fail. The ERC20 balance held in the module's escrow account becomes permanently unredeemable, while the corresponding Coins keep circulating freely (staking, IBC, bank transfers) with no way to reclaim the underlying asset — a textbook "token-pair-backed balance" permanent freeze.

### Impact Explanation
This matches the Critical impact gate for "permanent freezing, locking, theft, or unauthorized extraction of ... token-pair-backed balances." Once escrowed, the module-account ERC20 balance backing a `TokenPair` becomes permanently stuck for any token whose transfer does not preserve an exact byte-for-byte balance delta, with no code path to recover it (governance can only disable/toggle conversion via `ToggleConversion`, which does not unlock the already-escrowed tokens) [7](#0-6) .

### Likelihood Explanation
Likelihood is high: registration of arbitrary ERC20 contracts is permissionless by default configuration option, and fee-on-transfer/deflationary/rebasing ERC20 tokens are a well-known and common class of real-world tokens. Any unprivileged user can trigger the freeze simply by registering such a token as a pair and performing an ordinary `ConvertERC20`/`ConvertCoin` cycle — no privileged access or malicious relayer/validator is required.

### Recommendation
Replace the strict equality (`Cmp`/`Equal`) balance-invariance checks in `convertERC20IntoCoinsForNativeToken` and `ConvertCoinNativeERC20` with a `>=`/tolerant check that accounts for the actual (possibly reduced) amount received, and use that *actual received amount* — not the originally requested `amount` — for the corresponding coin mint/burn/escrow operation, so that partial-delivery tokens do not desynchronize the coin/ERC20 backing and are not blocked forever.

### Proof of Concept
1. Deploy a minimal ERC20 that charges a 1% fee on `transfer` (fee-on-transfer) or truncates balances by 1 wei due to internal rebase rounding, similar to OUSD.
2. With `PermissionlessRegistration` enabled, call `MsgRegisterERC20` to register this token as a `TokenPair`.
3. Mint tokens to a user, then call `MsgConvertERC20` to deposit — `k.evmKeeper.CallEVMWithData(... "transfer", ModuleAddress, amount)` delivers less than `amount` to the module account; `balanceTokenAfter.Cmp(expToken) != 0` triggers `ErrBalanceInvariance`, reverting the deposit permanently for any amount not exactly divisible.
4. Alternatively, if a token behavior change or rounding edge case allows one successful deposit (Coins minted, ERC20 escrowed), any subsequent `MsgConvertCoin` withdrawal by any holder of that coin permanently reverts at `x/erc20/keeper/msg_server.go:290-297`, permanently freezing the escrowed ERC20 balance under `types.ModuleAddress` while the Coin keeps circulating.

### Citations

**File:** x/erc20/keeper/msg_server.go (L86-130)
```go
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

**File:** x/erc20/keeper/msg_server.go (L262-297)
```go
	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}

	// Check unpackedRet execution
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return err
		}
		if !unpackedRet.Value {
			return sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute unescrow tokens from user")
		}
	}

	// Check expected Receiver balance after transfer execution
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceTokenAfter == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	exp := big.NewInt(0).Add(balanceToken, amount.BigInt())

	if r := balanceTokenAfter.Cmp(exp); r != 0 {
		return sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v", exp, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-345)
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
```

**File:** tests/integration/x/erc20/test_proposals.go (L128-155)
```go
		{
			"ok - governance, permissionless false",
			func() {
				s.network.App.GetErc20Keeper().SetPermissionlessRegistration(ctx, false)
			},
			authtypes.NewModuleAddress(govtypes.ModuleName).String(),
			true,
		},
		{
			"ok - governance, permissionless true",
			func() {},
			authtypes.NewModuleAddress(govtypes.ModuleName).String(),
			true,
		},
		{
			"fail - non-governance, permissionless false",
			func() {
				s.network.App.GetErc20Keeper().SetPermissionlessRegistration(ctx, false)
			},
			s.keyring.GetAccAddr(0).String(),
			false,
		},
		{
			"ok - non-governance, permissionless true",
			func() {},
			s.keyring.GetAccAddr(0).String(),
			true,
		},
```

**File:** tests/integration/x/erc20/test_proposals.go (L282-309)
```go
		{
			"disable conversion",
			func() {
				contractAddr, err = s.setupRegisterERC20Pair(contractMinterBurner)
				s.Require().NoError(err, "failed to register pair")
				ctx = s.network.GetContext()
				id = s.network.App.GetErc20Keeper().GetTokenPairID(ctx, contractAddr.String())
				pair, _ = s.network.App.GetErc20Keeper().GetTokenPair(ctx, id)
			},
			true,
			false,
		},
		{
			"disable and enable conversion",
			func() {
				contractAddr, err = s.setupRegisterERC20Pair(contractMinterBurner)
				s.Require().NoError(err, "failed to register pair")
				ctx = s.network.GetContext()
				id = s.network.App.GetErc20Keeper().GetTokenPairID(ctx, contractAddr.String())
				pair, _ = s.network.App.GetErc20Keeper().GetTokenPair(ctx, id)
				res, err := s.network.App.GetErc20Keeper().ToggleConversion(ctx, &types.MsgToggleConversion{Authority: authtypes.NewModuleAddress("gov").String(), Token: contractAddr.String()})
				s.Require().NoError(err)
				s.Require().NotNil(res)
				pair, _ = s.network.App.GetErc20Keeper().GetTokenPair(ctx, id)
			},
			true,
			true,
		},
```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-139)
```go
	// Case 2. native ERC20 token
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}

		pair, err := k.MintingEnabled(ctx, recipient, coin.Denom)
		if err != nil {
			ctx.EventManager().EmitEvent(
				sdk.NewEvent("erc20_callback_failure",
					sdk.NewAttribute(types.TypeMsgConvertCoin, "mint_failure"),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, coin.Denom),
					sdk.NewAttribute(types.AttributeKeyReceiver, recipient.String()),
				),
			)
			return channeltypes.NewErrorAcknowledgement(err)
		}

		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}
```
