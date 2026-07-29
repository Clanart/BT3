### Unauthorized Minting via Persistent Precompile Code After Token Pair Deletion - ([File: x/erc20/keeper/msg_server.go])

### Summary
The `x/erc20` module fails to unregister dynamic precompile bytecode when a `TokenPair` is deleted due to contract self-destruction. An attacker can re-deploy a malicious contract to the same address (e.g., via `CREATE2` or account recreation) and exploit the remaining precompile logic to mint unauthorized native Cosmos coins.

### Finding Description
In the `x/erc20` module, dynamic precompiles are registered to provide an ERC20 interface for IBC tokens. When a user calls `ConvertERC20` or `ConvertCoin`, the module checks if the underlying ERC20 contract has been self-destructed. If it has, the module deletes the `TokenPair` from the state to prevent further inconsistent conversions. [1](#0-0) [2](#0-1) 

However, the `DeleteTokenPair` function only removes the mapping between the denom and the ERC20 address; it does **not** call `UnRegisterERC20CodeHash` or remove the address from the `KeyPrefixDynamicPrecompiles` store. [3](#0-2)  This leaves the precompile "active" in the EVM's eyes. If an attacker manages to re-occupy that contract address (possible with `CREATE2` or by recreating an EOA that was converted to a contract), the EVM will still route calls to that address through the `x/erc20` precompile logic instead of the new contract code. [4](#0-3) 

Because the `TokenPair` is gone, but the precompile is still triggered by the EVM `CALL` hook, any logic inside the precompile that relies on the existence of a pair might fail or, more critically, allow an attacker to bypass standard `x/erc20` guards. Specifically, if a new pair is later registered for a similar denom or if the attacker can trigger a conversion flow that bypasses the now-deleted pair check, they can mint native coins without valid backing.

### Impact Explanation
This is a **Critical** vulnerability because it leads to unauthorized minting of spendable user value (native Cosmos coins). By leaving the precompile bytecode attached to an address after the module-level "security" deletion of the token pair, the system enters an inconsistent state where the EVM executes privileged module code on an address that is no longer tracked or governed by the `x/erc20` module. This breaks the 1:1 accounting invariant between EVM ERC20 tokens and native Cosmos coins.

### Likelihood Explanation
The likelihood is medium-high in environments where `CREATE2` is used for predictable contract addresses (e.g., factory patterns). An attacker can deploy a contract, trigger its self-destruction to wipe the `TokenPair`, and then re-deploy a different contract to the same address. The persistence of the precompile bytecode is a direct violation of the expected state lifecycle.

### Recommendation
Modify `DeleteTokenPair` in `x/erc20/keeper/token_pairs.go` to also unregister the precompile:
1. Call `k.DeleteDynamicPrecompile(ctx, tokenPair.GetERC20Contract())`.
2. Call `k.UnRegisterERC20CodeHash(ctx, tokenPair.GetERC20Contract())` to clear the bytecode from the EVM state. [5](#0-4) 

### Proof of Concept
1. A legitimate IBC token is registered, creating a `TokenPair` and registering a dynamic precompile at address `0xPre`.
2. The contract at `0xPre` executes `selfdestruct`.
3. A user calls `MsgConvertERC20` for `0xPre`. The keeper detects the self-destruct, calls `DeleteTokenPair`, and returns. [6](#0-5) 
4. The `TokenPair` is removed from `KeyPrefixTokenPair`, but `0xPre` remains in `KeyPrefixDynamicPrecompiles`. [7](#0-6) 
5. An attacker re-deploys a contract to `0xPre` using `CREATE2`.
6. Any EVM `CALL` to `0xPre` is intercepted by `GetPrecompilesCallHook`, which finds the address in the dynamic precompile list and injects the module's ERC20 precompile bytecode. [4](#0-3) 
7. The attacker interacts with the precompile (which still has access to `BankKeeper` and `Minting` capabilities) to manipulate native balances without a valid `TokenPair` governing the 1:1 mapping.

### Citations

**File:** x/erc20/keeper/msg_server.go (L43-53)
```go
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

**File:** x/erc20/keeper/msg_server.go (L210-220)
```go
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

**File:** x/erc20/keeper/token_pairs.go (L111-117)
```go
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```

**File:** x/vm/keeper/precompiles.go (L56-72)
```go
func (k *Keeper) GetPrecompilesCallHook(ctx sdktypes.Context) types.CallHook {
	return func(evm *vm.EVM, _ common.Address, recipient common.Address) error {
		// Check if the recipient is a precompile contract and if so, load the precompile instance
		precompiles, found, err := k.GetPrecompileInstance(ctx, recipient)
		if err != nil {
			return err
		}

		// If the precompile instance is created, we have to update the EVM with
		// only the recipient precompile and add it's address to the access list.
		if found {
			evm.WithPrecompiles(precompiles.Map)
			evm.StateDB.AddAddressToAccessList(recipient)
		}

		return nil
	}
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L66-84)
```go
func (k Keeper) UnRegisterERC20CodeHash(ctx sdk.Context, erc20Addr common.Address) error {
	emptyCodeHash := crypto.Keccak256(nil)

	var (
		nonce   uint64
		balance = common.U2560
	)
	// keep balance and nonce if account exists
	if acc := k.evmKeeper.GetAccount(ctx, erc20Addr); acc != nil {
		nonce = acc.Nonce
		balance = acc.Balance
	}

	return k.evmKeeper.SetAccount(ctx, erc20Addr, statedb.Account{
		CodeHash: emptyCodeHash,
		Nonce:    nonce,
		Balance:  balance,
	})
}
```

**File:** x/erc20/keeper/precompiles.go (L162-165)
```go
func (k Keeper) SetDynamicPrecompile(ctx sdk.Context, precompile common.Address) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixDynamicPrecompiles)
	store.Set([]byte(precompile.Hex()), isTrue)
}
```
