### Title
Permissionless ERC20 Token-Pair Registration Enables Attacker-Controlled Self-Destruct to Permanently Freeze Converted User Funds - (File: `x/erc20/keeper/msg_server.go`, `x/erc20/keeper/proposals.go`, `x/erc20/keeper/token_pairs.go`)

### Summary
The external report's core issue — a permissionless, unmodifiable registration process for a bridged token pair that a malicious contract deployer can weaponize to permanently strand user funds — has a direct analog in the Cosmos EVM `x/erc20` module. `RegisterERC20` can be called permissionlessly (governed by the `PermissionlessRegistration` param) to pair *any* attacker-deployed ERC20 contract with a freshly-minted native Cosmos coin denom. Because the token pair's validity is tied to the continued existence of that external contract's bytecode, the contract owner can self-destruct it at any point after legitimate users have converted ERC20 balances into the native coin representation. This deletes the token pair silently and irreversibly, permanently stranding the native coin balances that were minted 1:1 against the now-destroyed ERC20 token, with no pause/recovery mechanism — mirroring the exact "cannot be modified," "no pause for a specific pair," and "users losing their tokens" consequences described in the external report.

### Finding Description
1. **Permissionless, unvetted pair creation.** `RegisterERC20` in `x/erc20/keeper/msg_server.go:324-362` allows any signer to register an arbitrary ERC20 contract as a token pair when `params.PermissionlessRegistration` is true, only checking basic address validity and whether the ERC20/denom is already registered — no code/bytecode vetting is performed on the contract behind the pair. [1](#0-0) 

2. **`registerERC20`/`CreateCoinMetadata`** derive the bank denom (`erc20:0x<addr>`) purely from the contract's ABI-reported name/symbol/decimals and store the pair — again with no check that the contract cannot later be destroyed or altered. [2](#0-1) 

3. **Users legitimately convert ERC20 → native coin** via `ConvertERC20`, which escrows the ERC20 tokens on the module account and mints the corresponding native coin 1:1 to the receiver, protected by a balance-invariance check. [3](#0-2) 

4. **Contract self-destruction is unprivileged and unrestricted** — any EVM contract, including the very ERC20 registered as a token pair, can call `SELFDESTRUCT`, which is handled by `DeleteAccount`, clearing code, storage, and balance. [4](#0-3) 

5. **On the next `ConvertERC20`/`ConvertCoin` call touching that pair**, the code detects the missing code hash and silently deletes the token pair, returning `nil, nil` (no error) instead of reverting or preserving redemption rights:
```go
acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
if acc == nil || !acc.HasCodeHash() {
    k.DeleteTokenPair(ctx, pair)
    ...
    return nil, nil
}
``` [5](#0-4) [6](#0-5) 

6. **`DeleteTokenPair` removes the ERC20↔denom mapping and all allowances permanently** — there is no way to re-register the same denom/contract combination in a way that restores redeemability, and no admin/governance action exists to "pause" or freeze just this pair before damage occurs. [7](#0-6) 

Once the pair is deleted, any native coin balance of that specific `erc20:0x<addr>` denom still held by users (bank module state — untouched by `DeleteTokenPair`) is permanently unredeemable: `ConvertCoin` for that denom can never succeed again because the pair no longer exists to route the conversion, and the backing ERC20 contract bytecode is gone. This is functionally identical to the external report's "affected native token contract will never be bridgeable... users losing their tokens... no way to pause a specific pair."

### Impact Explanation
This qualifies as **Critical permanent freezing/locking of user funds**: native coin balances that were legitimately obtained through a supported conversion path become permanently stranded and unredeemable due to an unprivileged, attacker-controlled action (self-destructing their own registered contract) on a token pair that anyone was allowed to permissionlessly create. Unlike a generic "bad ERC20" scenario, this does not require Boba/Cosmos EVM admins to make a mistake — the attacker fully controls both the malicious contract's deployment/registration and the timing of its destruction, making this squarely an unprivileged, reachable path to unrecoverable value loss for third-party users who interacted with the registered pair before the self-destruct.

### Likelihood Explanation
Requires `PermissionlessRegistration=true` (a governance-configurable parameter) to allow the attacker to register the pair without needing governance authority; if this is `false`, the attack requires governance/authority approval of the malicious contract, reducing to a lower-likelihood, privileged-assumption scenario similar to the original NFTBridge report's acknowledged/registered risk. I was unable to confirm the shipped default value of `PermissionlessRegistration` in `x/erc20/types/params.go` within the available tool budget, so likelihood is contingent on that configuration being enabled (which is an explicit, documented, non-privileged feature of the module, not a misconfiguration).

### Recommendation
- Do not silently delete a token pair and drop redemption rights when the backing contract is destroyed; instead, disable only future ERC20-side operations while preserving a path (e.g., minting a fixed IOU or requiring governance-approved re-pairing) for outstanding native coin holders to recover value, or block registration/allow forced-disable rather than deletion.
- Add a "pause" capability for a specific token pair (as literally recommended in the original report) so governance can freeze conversions without deleting state, preserving forensic and recovery options.
- Consider requiring non-self-destructible bytecode (or a governance/allowlist review) for contracts eligible for `PermissionlessRegistration`, especially since `SELFDESTRUCT` is fully attacker-controlled here.

### Proof of Concept
1. With `PermissionlessRegistration=true`, attacker deploys a minimal ERC20 contract with a `mint`/`transfer` and a `selfdestruct(msg.sender)` function reachable by the deployer.
2. Attacker calls `MsgRegisterERC20` to register the contract; `registerERC20` creates denom `erc20:0x<attackerContract>` and stores the pair (`x/erc20/keeper/proposals.go:16-42`).
3. Attacker mints tokens to a victim (e.g. as part of an incentivized promotion) or victim independently acquires tokens on a DEX.
4. Victim calls `ConvertERC20` to convert ERC20 balance into the native `erc20:0x<attackerContract>` coin (`x/erc20/keeper/msg_server.go:71-140`), receiving minted native coins 1:1.
5. Attacker self-destructs the contract (`x/vm/keeper/statedb.go:249-286`).
6. Any subsequent `ConvertCoin` call for that denom hits the `acc == nil || !acc.HasCodeHash()` branch, deletes the token pair (`x/erc20/keeper/msg_server.go:209-220`, `x/erc20/keeper/token_pairs.go:110-117`), and returns `nil, nil` — victim's native coin balance is now permanently unredeemable, with no governance mechanism to restore it.

### Citations

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

**File:** x/erc20/keeper/proposals.go (L16-42)
```go
// RegisterERC20 creates a Cosmos coin and registers the token pair between the
// coin and the ERC20
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
}
```

**File:** x/vm/keeper/statedb.go (L243-286)
```go
// DeleteAccount handles contract's suicide call:
// - clear balance
// - remove code
// - remove states
// - remove the code hash
// - remove auth account
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum contracts can be self-destructed
	if !k.IsContract(ctx, addr) {
		return errors.New("only smart contracts can be self-destructed")
	}

	// set account to a base account to set the whole balance as spendable
	baseAccount := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	k.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(cosmosAddr, baseAccount.GetPubKey(), baseAccount.GetAccountNumber(), baseAccount.GetSequence()))

	// clear balance
	if err := k.SetBalance(ctx, addr, new(uint256.Int)); err != nil {
		return err
	}

	var keys []common.Hash

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		keys = append(keys, key)
		return true
	})

	for _, key := range keys {
		k.DeleteState(ctx, addr, key)
	}

	// clear code hash
	k.DeleteCodeHash(ctx, addr)

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)
```

**File:** x/erc20/keeper/token_pairs.go (L110-117)
```go
// DeleteTokenPair removes a token pair.
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```
