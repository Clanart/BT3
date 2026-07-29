### Title
Unauthorized native-coin minting via EIP-7702 code-swap on a registered "native ERC20" token pair address - (File: `x/erc20/keeper/msg_server.go`, `x/erc20/keeper/proposals.go`)

### Summary
The report describes a class of bug where an address's "registered/privileged" status is bound only to the address, not to the underlying entity's identity/bytecode, allowing an attacker to swap out what lives behind that address after registration and inherit the privilege. In Cosmos EVM, the `x/erc20` module's permissionless native-ERC20 registration (`registerERC20`) and its conversion invariant checks (`ConvertERC20`, `ConvertCoin`) bind a `TokenPair` to a contract **address**, and only check whether that address currently `HasCodeHash()` to decide whether the pair is still "alive" — never that the code is the same code that was present at registration time. EIP-7702 allows an EOA to point its "code" (a delegation designator) to arbitrary logic at will, and to change that pointer again later in a completely separate transaction, with no selfdestruct/re-creation restriction. This lets an attacker register a legitimately-behaving ERC20 implementation, build up real escrowed value/trust through normal conversions, then redirect the same address to self-serving fake logic and mint unlimited native coin.

### Finding Description
- `registerERC20` (`x/erc20/keeper/proposals.go:18-41`) only checks `IsERC20Registered` and derives metadata via `CreateCoinMetadata`/`QueryERC20`; it never verifies the target is an immutable, non-delegatable contract. [1](#0-0)  `CreateCoinMetadata` similarly never checks `IsContract`, only that ABI calls succeed. [2](#0-1) 
- `RegisterERC20` (`x/erc20/keeper/msg_server.go:326-362`) is permissionless when `PermissionlessRegistration` is enabled, so any address (including an EOA using EIP-7702) can be registered as a native ERC20 token pair. [3](#0-2) 
- `ConvertERC20`/`ConvertCoin` only guard against a fully-destroyed contract by checking `!acc.HasCodeHash()`, deleting the pair only if code is entirely absent: [4](#0-3) [5](#0-4) 
- `x/vm`'s `IsContract` explicitly treats EIP-7702-delegated code differently (`ParseDelegation`), but this distinction is never consulted by the erc20 module's registration or conversion path: [6](#0-5) 
- The `convertERC20IntoCoinsForNativeToken`/`ConvertCoinNativeERC20` "balance invariance" checks call `k.BalanceOf` against the *same* contract address whose logic the attacker controls, so they only prove internal self-consistency of the (attacker-controlled) contract, not that any real value moved: [7](#0-6) [8](#0-7) 

Because the delegation designator of an EIP-7702 account can be re-authorized in any subsequent transaction by the EOA owner (no selfdestruct or address-recreation needed — this is a first-class EVM feature, confirmed by the SetCode integration tests), the attacker can:
1. Point their EOA at a genuine, well-behaved ERC20 implementation.
2. Register it via `MsgRegisterERC20` (permissionless).
3. Perform legitimate `ConvertERC20`/`ConvertCoin` round trips to establish escrowed native-coin/token balances in the module account and build ecosystem trust (liquidity, third-party usage of the paired denom).
4. Submit a new SetCode authorization repointing the same address to malicious logic whose `transfer`/`balanceOf` always report success/incrementing balances without any real value backing.
5. Repeatedly call `ConvertERC20` against the now-malicious "same" registered contract to trigger `k.bankKeeper.MintCoins` and drain/inflate the paired native coin denom, since the balance-invariance checks are satisfied trivially by the self-controlled fake contract.

This exactly mirrors the report's root cause: a registration/authorization decision made once at address-level is treated as permanently valid for that address, while the actual entity behind the address is mutable and attacker-controlled after the fact.

### Impact Explanation
This allows unauthorized, unbounded minting of the native Cosmos coin backing a native-ERC20 token pair (`k.bankKeeper.MintCoins` in `convertERC20IntoCoinsForNativeToken`), a direct "Critical unauthorized minting... of spendable user value across native balances... or token-pair-backed balances" per the impact gate. It also permits extraction/burn manipulation on the `ConvertCoin` path, letting the attacker fraudulently reclaim/burn escrowed coins without providing genuine token value, corrupting the 1:1 accounting invariant between the native coin and its "ERC20 representation" that `x/erc20` is designed to preserve.

### Likelihood Explanation
Requires: (a) `PermissionlessRegistration` enabled (a supported, documented operating mode, not a misconfiguration) or governance approval, and (b) EIP-7702 SetCode transactions enabled on the chain (a first-class, already-integration-tested EVM feature in this repo). No privileged role, relayer, or validator collusion is needed — any unprivileged EOA holder can execute the full chain of steps using only standard `MsgRegisterERC20`, `MsgConvertERC20`/`MsgConvertCoin`, and EVM `SetCode` transactions.

### Recommendation
- In `registerERC20`/`CreateCoinMetadata`, require `evmKeeper.IsContract(ctx, contract)` to be true (which already excludes EIP-7702-delegated addresses) before allowing native ERC20 registration.
- In `ConvertERC20`/`ConvertCoin`, in addition to checking `HasCodeHash()`, persist and re-validate the code hash (or reject delegated/EIP-7702 accounts) recorded at registration time on every conversion, invalidating/removing the token pair if the code hash changes.
- Consider explicitly rejecting `ParseDelegation`-flagged accounts anywhere a "native ERC20 contract" address is accepted as input.

### Proof of Concept
1. Attacker EOA `A` sends a Type-4 (SetCode) transaction authorizing delegation to a legitimate `ERC20MinterBurnerDecimals`-compatible contract `L`, making `A`'s on-chain code resolve to `L`'s logic (per `checkSetCode` behavior demonstrated in the EIP-7702 integration tests). [9](#0-8) 
2. Attacker submits `MsgRegisterERC20{Signer: A, Erc20Addresses: [A]}` (permissionless mode) — passes because `CreateCoinMetadata`/`QueryERC20` succeed against the delegated code and no `IsContract` gate exists. [10](#0-9) 
3. Attacker performs one or more genuine `MsgConvertCoin`/`MsgConvertERC20` cycles to escrow value and establish trust/liquidity for the new token pair. [11](#0-10) 
4. Attacker sends a new SetCode authorization from `A` pointing to malicious contract `M`, whose `transfer` always returns `true`/emits a fake Transfer event and whose `balanceOf` always returns whatever satisfies the caller's expected delta.
5. Attacker calls `MsgConvertERC20` repeatedly against contract `A` (now backed by `M`): the `!acc.HasCodeHash()` guard still passes (code is still present, just different), the "transfer to module" call to `M` fakes success, and the balance-invariance check against `M.balanceOf` is self-satisfied, so `k.bankKeeper.MintCoins`/`SendCoinsFromModuleToAccount` mint and pay out real native coin to the attacker with no genuine token backing. [12](#0-11)

### Citations

**File:** x/erc20/keeper/proposals.go (L18-41)
```go
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
```

**File:** x/erc20/keeper/proposals.go (L46-69)
```go
func (k Keeper) CreateCoinMetadata(
	ctx sdk.Context,
	contract common.Address,
) (*banktypes.Metadata, error) {
	strContract := contract.String()

	erc20Data, err := k.QueryERC20(ctx, contract)
	if err != nil {
		return nil, err
	}

	// Check if metadata already exists
	_, found := k.bankKeeper.GetDenomMetaData(ctx, types.CreateDenom(strContract))
	if found {
		return nil, errorsmod.Wrap(
			types.ErrInternalTokenPair, "denom metadata already registered",
		)
	}

	if k.IsDenomRegistered(ctx, types.CreateDenom(strContract)) {
		return nil, errorsmod.Wrapf(
			types.ErrInternalTokenPair, "coin denomination already registered: %s", erc20Data.Name,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L42-53)
```go
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L63-188)
```go
// convertERC20IntoCoinsForNativeToken handles the erc20 conversion for a native erc20 token
// pair:
//   - escrow tokens on module account
//   - mint coins on bank module
//   - send minted coins to the receiver
//   - check if coin balance increased by amount
//   - check if token balance decreased by amount
//   - check for unexpected `Approval` event in logs
func (k Keeper) convertERC20IntoCoinsForNativeToken(
	ctx sdk.Context,
	pair types.TokenPair,
	msg *types.MsgConvertERC20,
	receiver sdk.AccAddress,
	sender common.Address,
) (*types.MsgConvertERC20Response, error) {
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

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}

	// Check expected receiver balance after transfer
	balanceCoinAfter := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	expCoin := balanceCoin.Add(coins[0])

	if ok := balanceCoinAfter.Equal(expCoin); !ok {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid coin balance - expected: %v, actual: %v",
			expCoin, balanceCoinAfter,
		)
	}

	defer func() {
		telemetry.IncrCounterWithLabels(
			[]string{"tx", "msg", "convert", "erc20", "total"},
			1,
			[]metrics.Label{
				telemetry.NewLabel("coin", pair.Denom),
			},
		)

		if msg.Amount.IsInt64() {
			telemetry.IncrCounterWithLabels(
				[]string{"tx", "msg", "convert", "erc20", "amount", "total"},
				float32(msg.Amount.Int64()),
				[]metrics.Label{
					telemetry.NewLabel("denom", pair.Denom),
				},
			)
		}
	}()

	ctx.EventManager().EmitEvents(
		sdk.Events{
			sdk.NewEvent(
				types.EventTypeConvertERC20,
				sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
				sdk.NewAttribute(types.AttributeKeyReceiver, msg.Receiver),
				sdk.NewAttribute(sdk.AttributeKeyAmount, msg.Amount.String()),
				sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
				sdk.NewAttribute(types.AttributeKeyERC20Token, msg.ContractAddress),
			),
		},
	)

	return &types.MsgConvertERC20Response{}, nil
}
```

**File:** x/erc20/keeper/msg_server.go (L209-220)
```go
	case pair.IsNativeERC20():
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L251-297)
```go
	balanceToken := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceToken == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

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

**File:** x/erc20/keeper/msg_server.go (L324-362)
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

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeRegisterERC20,
				sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
				sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
			),
		)
	}

	return &types.MsgRegisterERC20Response{}, nil
}
```

**File:** x/vm/keeper/utils.go (L10-18)
```go
// IsContract determines if the given address is a smart contract.
// It checks if the account has associated code and ensures that
// the code is not a delegated contract (EIP-7702).
func (k *Keeper) IsContract(ctx sdk.Context, addr common.Address) bool {
	codeHash := k.GetCodeHash(ctx, addr)
	code := k.GetCode(ctx, codeHash)

	_, delegated := ethtypes.ParseDelegation(code)
	return len(code) > 0 && !delegated
```

**File:** tests/integration/eip7702/test_integration.go (L103-118)
```go
		Context("if input address is EoA address", func() {
			It("should succeed", func() {
				acc0, err := s.grpcHandler.GetEvmAccount(user0.Addr)
				Expect(err).To(BeNil())

				authorization := s.createSetCodeAuthorization(validChainID, acc0.GetNonce()+1, user1.Addr)
				signedAuthorization, err := s.signSetCodeAuthorization(user0, authorization)
				Expect(err).To(BeNil())

				err = s.sendSetCodeTx(user0, signedAuthorization)
				Expect(err).To(BeNil(), "error while sending SetCode tx")
				Expect(s.network.NextBlock()).To(BeNil())

				s.checkSetCode(user0, user1.Addr, true)
			})
		})
```
