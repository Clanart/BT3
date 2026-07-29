## Title
Malicious registered ERC20 token can spoof `balanceOf` to mint unlimited native Cosmos coins in `ConvertERC20` - (File: `x/erc20/keeper/msg_server.go`)

### Summary
The FNDZStaking report's core lesson is that state-changing effects must not depend on values that are only trustworthy *before* an external call to a potentially attacker-controlled contract. The `x/erc20` module's `convertERC20IntoCoinsForNativeToken` function (invoked by the permissionless `MsgConvertERC20` handler) has the same structural flaw: it calls into an arbitrary, user-registered ERC20 contract, and then uses that *same untrusted contract's* self-reported `balanceOf` return values as the sole invariant check before minting real native Cosmos coins and disbursing them to the caller.

### Finding Description
`ConvertERC20` → `convertERC20IntoCoinsForNativeToken` in [1](#0-0)  performs, in order:

1. Reads `balanceToken` via `k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)` — a `STATICCALL`/`CallEVM` into the **attacker-supplied** ERC20 contract.
2. Calls `k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)` to execute `transfer(ModuleAddress, amount)` on that same attacker-controlled contract [2](#0-1) .
3. Re-reads `balanceTokenAfter` from the **same** attacker contract and checks it equals `balanceToken + amount` [3](#0-2) .
4. Only after that self-reported check passes, it calls `k.bankKeeper.MintCoins` and `SendCoinsFromModuleToAccount`, disbursing real, spendable native Cosmos coins [4](#0-3) .

Because the "before" and "after" escrow-balance values used to validate the transfer are queried from the very contract being invoked, an attacker who controls that contract's bytecode can make `balanceOf` and `transfer` return arbitrary values that always satisfy the invariant check, regardless of whether any real token value was moved. Token pairs for arbitrary ERC20 contracts can be registered without owning real backing value: `RegisterERC20` is **permissionless** when the `PermissionlessRegistration` param is enabled — "Any account can permissionlessly register a native ERC20 contract to map to a Cosmos Coin" [5](#0-4) , and `MintingEnabled`/`ConvertERC20` perform no additional validation of the ERC20 contract's actual balance-tracking correctness beyond the self-reported values [6](#0-5) .

This mirrors the FNDZStaking bug class precisely: an external call to an attacker-influenced contract is trusted to report the state needed to gate a critical, irreversible state update (there: reward flags; here: bank `MintCoins`), instead of relying on values computed independently by the protocol.

### Impact Explanation
This allows an unprivileged attacker to:
1. Deploy a malicious ERC20 contract whose `transfer()` always returns `true` (or emits a fake `Transfer` event) without actually debiting balance, and whose `balanceOf(ModuleAddress)` returns whatever value is needed to satisfy `balanceTokenAfter == balanceToken + amount` on every call.
2. Permissionlessly register this contract as a token pair via `MsgRegisterERC20` (when `PermissionlessRegistration=true`).
3. Repeatedly call `MsgConvertERC20` with arbitrarily large `amount` values, each time passing the self-reported balance invariant and causing `bankKeeper.MintCoins` to mint real, spendable native coins that are then sent to the attacker.

This is unauthorized, unbacked minting of spendable native chain value — a Critical accounting-corruption impact matching the required impact gate (unauthorized minting/duplication of spendable value backed by nothing).

### Likelihood Explanation
High. No privileged role is required beyond deploying and registering an ordinary contract (gated only by the `PermissionlessRegistration` governance parameter, which multiple code paths and tests indicate is a supported non-privileged mode). No reentrancy or gas-griefing tricks are needed — the attacker's contract can simply return fabricated data on every call. This is directly reachable through the standard `MsgConvertERC20` message handler.

### Recommendation
Do not rely on the target ERC20 contract's own `balanceOf`/`transfer` return values as the sole correctness proof for minting. Options:
- Track escrowed token amounts using the module's own bank-keeper/native-side accounting (as already done for `pair.IsNativeCoin()` cases) instead of trusting external contract state for `IsNativeERC20()` pairs.
- Require token pairs to be backed by contracts with verified/allow-listed bytecode (e.g., only the module-generated `ERC20MinterBurnerDecimalsContract`, or a bytecode-hash check) rather than permissionless registration of arbitrary bytecode when this flow can mint real value.
- If permissionless registration must remain, cap `ConvertERC20`-driven minting per token pair to a supply invariant that is independently tracked and cannot be inflated purely by self-reported `balanceOf` results from the registered contract.

### Proof of Concept
1. Deploy contract `FakeToken` implementing:
   - `transfer(address,uint256) returns (bool)` → always returns `true`, does not modify any real balance.
   - `balanceOf(address who) returns (uint256)` → returns a counter that increases by the exact `amount` requested on each successive call (attacker can simulate this deterministically since they control the call data/`msg.sender` awareness), satisfying `balanceTokenAfter == balanceToken + amount` every time.
2. With `PermissionlessRegistration` enabled, call `MsgRegisterERC20{Erc20Addresses: [FakeToken]}` to create a `TokenPair` for `FakeToken` ↔ some native denom (e.g. `erc20/<address>`).
3. Repeatedly submit `MsgConvertERC20{ContractAddress: FakeToken, Amount: X, Receiver: attacker, Sender: attacker}`.
4. Each call passes the `balanceTokenAfter`/`expToken` check in [3](#0-2)  without any real token custody change, and `bankKeeper.MintCoins`/`SendCoinsFromModuleToAccount` mints and delivers `X` new native coins to the attacker's account, indefinitely.

**Note on uncertainty:** I was not able to fully verify within the tool budget (a) the default value of the `PermissionlessRegistration` parameter at genesis for this specific chain configuration, or (b) whether any additional bytecode/interface validation exists elsewhere in `registerERC20` (`x/erc20/keeper/proposals.go`) that might reject non-standard `balanceOf`/`transfer` implementations. If `PermissionlessRegistration` defaults to `false` and governance approval is required for every token pair, the practical likelihood is reduced to "requires a governance-approved malicious token," which would fall outside the unprivileged-trigger requirement — this should be confirmed against `x/erc20/types/params.go` defaults and `proposals.go`'s `registerERC20` validation logic before treating this as fully unprivileged-exploitable.

### Citations

**File:** x/erc20/keeper/msg_server.go (L71-140)
```go
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

**File:** x/erc20/keeper/mint.go (L18-66)
```go
func (k Keeper) MintingEnabled(
	ctx sdk.Context,
	receiver sdk.AccAddress,
	token string,
) (types.TokenPair, error) {
	if !k.IsERC20Enabled(ctx) {
		return types.TokenPair{}, errorsmod.Wrap(
			types.ErrERC20Disabled, "module is currently disabled by governance",
		)
	}

	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}

	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}

	if k.bankKeeper.BlockedAddr(receiver.Bytes()) {
		return types.TokenPair{}, errorsmod.Wrapf(
			errortypes.ErrUnauthorized, "%s is not allowed to receive transactions", receiver,
		)
	}

	// NOTE: ignore amount as only denom is checked on IsSendEnabledCoin
	coin := sdk.Coin{Denom: pair.Denom}

	// check if minting to a recipient address other than the sender is enabled
	// for for the given coin denom
	if !k.bankKeeper.IsSendEnabledCoin(ctx, coin) {
		return types.TokenPair{}, errorsmod.Wrapf(
			banktypes.ErrSendDisabled, "minting '%s' coins to an external address is currently disabled", token,
		)
	}

	return pair, nil
```
