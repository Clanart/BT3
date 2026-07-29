## Analog Identified: Stale ERC20 contract mapping trusted after selfdestruct + CREATE2 redeploy

### Title
Unauthorized minting via TokenPair mapping that is never re-validated against contract code after selfdestruct/redeploy - (File: `x/erc20/keeper/msg_server.go`)

### Summary
The audited DSProxyCache bug is a data-validation flaw: a cache maps a hash/identifier to a contract *address*, but nothing pins that address to the specific bytecode that was vetted, so an attacker can destroy and replace the contract while the cache continues to be trusted. The `x/erc20` module has the same class of flaw: a `TokenPair` permanently maps a Cosmos denom to an EVM contract *address* [1](#0-0) , and the only "liveness" check performed before trusting that address for minting/burning conversions is whether the account currently `HasCodeHash()` — not whether the code matches what was reviewed/registered originally.

### Finding Description
When registering a native ERC20 token pair, the keeper simply queries `name/symbol/decimals` from the contract and stores the pairing; no code hash is captured or pinned to the `TokenPair` record: [2](#0-1) 

Registration is permissionless by default (`PermissionlessRegistration`), so any unprivileged account can register any contract they control as `OWNER_EXTERNAL`: [3](#0-2) 

The only guard against a self-destructed contract is in `ConvertERC20`/`ConvertCoin`, which checks only `acc == nil || !acc.HasCodeHash()` and lazily deletes the token pair — it never checks that the code hash matches the one that was present at registration time: [4](#0-3) [5](#0-4) 

The actual conversion logic (`convertERC20IntoCoinsForNativeToken` / `ConvertCoinNativeERC20`) trusts whatever contract currently lives at `pair.GetERC20Contract()` to answer `balanceOf`/`transfer` truthfully, and mints/unlocks native coins purely based on that contract's self-reported balance deltas: [6](#0-5) [7](#0-6) 

Since the EVM supports `CREATE2`/`SELFDESTRUCT` natively (also demonstrated in the codebase's own `Reverter.sol` test fixture using deterministic CREATE2 addressing) [8](#0-7) , an attacker who deployed the originally-registered contract via `CREATE2` can:
1. Register a legitimate-looking ERC20 contract `A` at deterministic address `addr` as a `TokenPair`.
2. Self-destruct `A`.
3. Redeploy completely different, malicious bytecode `B` at the same `addr` (same deployer/salt).
4. Call `MsgConvertERC20`/`MsgConvertCoin` — the keeper's only staleness check (`HasCodeHash()`) now passes because `B` has code, and the `TokenPair` (denom↔`addr` mapping) is never revalidated against original bytecode.
5. `B`'s spoofed `transfer`/`balanceOf` responses drive `k.bankKeeper.MintCoins`/unescrow logic, letting the attacker mint or unlock native/bank-denominated value with no real backing ERC20 balance.

This is the same root cause as DSProxyCache: a persistent mapping (cache) from an identifier to a contract *address* is treated as an implicit guarantee about the contract's *behavior*, but nothing enforces that the code backing that address hasn't been swapped via selfdestruct + redeploy.

### Impact Explanation
If exploited, this allows unauthorized minting of native bank coins (`k.bankKeeper.MintCoins` in `convertERC20IntoCoinsForNativeToken`) or unauthorized unescrow of previously-escrowed coins (`ConvertCoinNativeERC20`) backed by a spoofed ERC20 contract, i.e., critical unauthorized minting/duplication of spendable user value via a precompile-mediated/ERC20 representation path, matching the in-scope Critical impact class.

### Likelihood Explanation
Requires the attacker to register (or otherwise control) a self-destructible contract as a `TokenPair`, which is trivially possible since ERC20 registration is permissionless by default and there is no code-hash pinning or re-verification anywhere in the conversion flow. No privileged role is needed — an ordinary user can execute the entire sequence (`RegisterERC20` → mint/convert to build up denom usage → self-destruct → CREATE2 redeploy → convert) via ordinary transactions.

### Recommendation
Capture and pin the contract's `codehash` (or full bytecode hash) in the `TokenPair` at registration time, and re-verify that the currently deployed code at `pair.GetERC20Contract()` still matches that hash before allowing any `ConvertERC20`/`ConvertCoin` execution — analogous to the report's recommendation that a proxy/cache should only interact with non-destructible (or codehash-pinned) contracts.

### Proof of Concept
1. Deploy a `Factory` contract that can `CREATE2` deploy arbitrary bytecode with a fixed salt.
2. Use `Factory` to deploy `TokenA` (standards-compliant ERC20 with a hidden `kill()` calling `selfdestruct`) at deterministic address `addr`.
3. Call `MsgRegisterERC20{Erc20Addresses: [addr]}` (permissionless) to create `TokenPair(denom, addr, OWNER_EXTERNAL)`.
4. Call `TokenA.kill()` to self-destruct it.
5. Use `Factory` to `CREATE2` redeploy `TokenB` at the same `addr`, with a `transfer`/`balanceOf` implementation that always reports success/large balances without real accounting.
6. Call `MsgConvertERC20` for a large amount targeting `addr` — `ConvertERC20` sees `acc.HasCodeHash() == true` (from `TokenB`) and proceeds; `convertERC20IntoCoinsForNativeToken` accepts `TokenB`'s spoofed `transfer` success/balance delta and calls `k.bankKeeper.MintCoins`, minting native coins with no real ERC20 backing.

### Citations

**File:** x/erc20/types/token_pair.go (L31-39)
```go
// NewTokenPair returns an instance of TokenPair
func NewTokenPair(erc20Address common.Address, denom string, contractOwner Owner) TokenPair {
	return TokenPair{
		Erc20Address:  erc20Address.String(),
		Denom:         denom,
		Enabled:       true,
		ContractOwner: contractOwner,
	}
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

**File:** x/erc20/keeper/msg_server.go (L78-141)
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

**File:** x/erc20/keeper/msg_server.go (L248-306)
```go
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()

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

	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}

	return nil
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

**File:** contracts/solidity/precompiles/testutil/contracts/Reverter.sol (L43-63)
```text
    // calculates the CREATE2 address of deploying Transferer with some salt
    function predictAddress(bytes32 salt) internal view returns (address) {
        address predictedAddress = address(
            uint160(
                uint(
                    keccak256(
                        abi.encodePacked(
                            bytes1(0xff),
                            address(this),
                            salt,
                            keccak256(
                                abi.encodePacked(type(Transferer).creationCode)
                            )
                        )
                    )
                )
            )
        );

        return predictedAddress;
    }
```
