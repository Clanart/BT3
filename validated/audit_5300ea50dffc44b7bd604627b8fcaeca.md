### Title
Non-EVM-Denom Bank Balances Orphaned After SELFDESTRUCT Enable Theft via CREATE2 Address Reuse - (File: x/evm/statedb/statedb.go)

### Summary

When a contract self-destructs, `StateDB.Commit()` only burns the EVM-denom balance at the destroyed address. Non-EVM-denom tokens (IBC, CosmWasm bridge tokens, or any other Cosmos bank-module coins) held by the destroyed address are explicitly left as orphaned bank balances. An attacker can exploit this by deploying a contract at a predictable CREATE2 address, accumulating non-EVM-denom tokens at that address, triggering SELFDESTRUCT (EIP-6780 compliant, within the same transaction), and then redeploying a malicious contract at the same CREATE2 address to drain the orphaned tokens via a bank precompile or native action.

### Finding Description

In `StateDB.Commit()`, the self-destruct handling path explicitly only burns the EVM-denom balance and then calls `DeleteAccount()`:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
    }
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
    return errorsmod.Wrap(err, "failed to delete account")
}
``` [1](#0-0) 

`DeleteAccount()` in the keeper removes only the auth account record and EVM storage slots. It never touches the Cosmos bank module balances for any denomination:

```go
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    // ...
    // clear storage
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
        k.SetState(ctx, addr, key, nil)
        return true
    })
    // remove auth account
    k.accountKeeper.RemoveAccount(ctx, acct)
    // NOTE: balance should be cleared separately
    ...
}
``` [2](#0-1) 

In the Cosmos SDK bank module, balances are stored keyed by address, not by account type. When the auth account is removed but the bank balance is not, the coins remain at that address in the bank store. When a new contract is subsequently deployed at the same address (via CREATE2 with the same factory and salt), the new contract's corresponding Cosmos address inherits those orphaned bank balances. If the chain exposes a bank precompile (a supported and documented pattern in Ethermint), the new contract can call it to transfer those tokens to an attacker-controlled address. [3](#0-2) 

### Impact Explanation

This is a **Critical** impact matching: *"Unauthorized theft, mint, burn bypass, or balance transfer of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic."*

Any non-EVM-denom Cosmos bank coins (IBC tokens, staking tokens, governance tokens, or any custom Cosmos SDK coin) held by a self-destructed contract address are permanently orphaned in the bank module. They can be stolen by any party who redeploys a contract at the same CREATE2 address and uses a bank precompile or native action to drain them. The theft is complete and irreversible once the redeploy transaction commits.

### Likelihood Explanation

The attack is reachable via a standard unprivileged EVM transaction sequence:

1. Deploy a factory contract (standard CREATE2 factory pattern).
2. Deploy a child contract via CREATE2 with a known salt.
3. Cause the child to accumulate non-EVM-denom tokens (e.g., IBC tokens sent to it by any user, or received via a Cosmos-native transfer to the contract's bech32 address).
4. In the same transaction as deployment (satisfying EIP-6780), call SELFDESTRUCT on the child.
5. In a subsequent transaction, redeploy the child at the same CREATE2 address.
6. Call a bank precompile from the new child to drain the orphaned tokens.

The Ethermint documentation explicitly describes and supports bank precompiles as a first-class integration pattern. [4](#0-3) 

The code comment itself acknowledges the gap: *"Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances."* [5](#0-4) 

### Recommendation

In `StateDB.Commit()`, before calling `DeleteAccount()`, enumerate **all** bank-module coin balances held by the destroyed address (not just the EVM denom) and burn or transfer them. The Cosmos SDK `BankKeeper.GetAllBalances()` can be used to retrieve all denominations. Each non-zero balance should be burned via `SendCoinsFromAccountToModule` + `BurnCoins`, or transferred to a designated treasury/community pool, within the same `CacheContext` that wraps the `DeleteAccount` call to ensure atomicity.

### Proof of Concept

```
Block N:
  Tx1 (attacker):
    1. Deploy FactoryContract (standard CREATE2 factory)
    2. Call factory.deployAndDestroy(salt):
       a. CREATE2 deploys ChildContract at addr = CREATE2(factory, salt, initcode)
       b. ChildContract.selfdestruct() — valid under EIP-6780 (created in same tx)
       c. factory.call{value: 0}(addr) — sends IBC tokens to addr via bank precompile
          (or IBC tokens were already at addr from a prior transfer)
    3. After Commit(): ChildContract auth account deleted, EVM denom burned,
       but IBC tokens remain at addr in bank module.

Block N+1:
  Tx2 (attacker):
    1. Call factory.redeploy(salt):
       a. CREATE2 redeploys MaliciousChild at same addr
    2. Call MaliciousChild.drain():
       a. Calls bank precompile: transfer(addr, attacker, ibcTokenBalance)
    3. Attacker receives all orphaned IBC tokens.
```

The existing integration test `test_selfdestruct_recreated_address_cannot_recover_funds` only verifies that the **EVM-denom** balance is zero after redeploy. It does not test non-EVM-denom balances, leaving the orphaned-token path untested and exploitable. [6](#0-5)

### Citations

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** x/evm/keeper/statedb.go (L189-222)
```go
// DeleteAccount handles contract's suicide call:
// - remove code
// - remove states
// - remove auth account
//
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum accounts (contracts) can be selfdestructed
	_, ok := acct.(ethermint.EthAccountI)
	if !ok {
		return errorsmod.Wrapf(types.ErrInvalidAccount, "type %T, address %s", acct, addr)
	}

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		k.SetState(ctx, addr, key, nil)
		return true
	})

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.debugLog(ctx, "account suicided",
		"ethereum-address", addr,
		"cosmos-address", cosmosAddr,
	)

	return nil
```

**File:** docs/precompile_creation_guide.md (L301-351)
```markdown
- `balanceOf`: 10_000
- `transfer`: 150_000

### Contract struct and factory

```go
type BankContract struct {
	bankKeeper  types.BankKeeper
	cdc         codec.Codec
	kvGasConfig storetypes.GasConfig
}

func NewBankContract(bankKeeper types.BankKeeper, cdc codec.Codec, kvGasConfig storetypes.GasConfig) vm.PrecompiledContract {
	return &BankContract{bankKeeper, cdc, kvGasConfig}
}
```

`Address()` returns the fixed address; `RequiredGas(input)` uses `input[:4]` as method ID and returns the mapped gas plus a base cost (`len(input) * kvGasConfig.WriteCostPerByte`).

### Run: ExtStateDB and method dispatch

1. **Cast StateDB** – `stateDB := evm.StateDB.(ExtStateDB)` (from `precompiles/interface.go`).
2. **Dispatch by method** – `method, _ := bankABI.MethodById(contract.Input[:4])`, then `method.Inputs.Unpack(contract.Input[4:])`, and a `switch method.Name`.
3. **Read-only (`balanceOf`)** – Uses `stateDB.Context()` only:  
   `bc.bankKeeper.GetBalance(stateDB.Context(), account, EVMDenom(token))`, then `method.Outputs.Pack(balance)`.
4. **State-changing (`mint`, `burn`, `transfer`)** – Returns an error if `readonly` is true. Then runs all Cosmos logic inside **`stateDB.ExecuteNativeAction(precompileAddr, nil, func(ctx sdk.Context) error { ... })`**:
   - **mint**: `IsSendEnabledCoins` → `MintCoins(module, ...)` → `SendCoinsFromModuleToAccount`.
   - **burn**: `SendCoinsFromAccountToModule` → `BurnCoins(module, ...)`.
   - **transfer**: `IsSendEnabledCoins` → `SendCoins(from, to, coins)`.
   - Uses `EVMDenom(contract.Caller())` as the coin denom and checks `bankKeeper.BlockedAddr` for recipients.
5. **Return** – `method.Outputs.Pack(true)` for mutating methods, or the balance for `balanceOf`.

So: reads go through `Context()`; writes go through `ExecuteNativeAction` so that any failure (or EVM revert) reverts Cosmos state as well.

### Integration in the app

To register the bank precompile, pass a factory in `customContractFns` that injects the bank keeper, codec, and gas config (e.g. from `storetypes.TransientGasConfig()` or a module param):

```go
gasConfig := storetypes.TransientGasConfig() // or your app's config

[]evmkeeper.CustomContractFn{
	// ... other precompiles (e.g. Relayer, ICA) ...
	func(_ sdk.Context, rules ethparams.Rules) vm.PrecompiledContract {
		return cronosprecompiles.NewBankContract(app.BankKeeper, appCodec, gasConfig)
	},
},
```

The EVM keeper then builds each EVM instance with default precompiles plus this bank precompile; calls to `0x64` with ABI-encoded `mint`, `burn`, `balanceOf`, or `transfer` are handled by the bank precompile’s `Run` and the Cosmos x/bank module.

```

**File:** tests/integration_tests/test_selfdestruct.py (L81-122)
```python
def test_selfdestruct_recreated_address_cannot_recover_funds(ethermint, geth):
    """
    Recreating the child at the same CREATE2 address must not expose any
    preserved balance to the new contract.
    """
    salt = bytes(31) + b"\x02"
    value = 10**9

    def process(w3):
        factory, child_addr, _ = _run(w3, salt, value)
        assert w3.eth.get_balance(child_addr) == 0

        validator_balance_before = w3.eth.get_balance(ADDRS["validator"])

        redeploy_receipt = send_transaction(
            w3,
            factory.functions.redeployChild(salt).build_transaction(
                {"from": ADDRS["validator"]}
            ),
            KEYS["validator"],
        )
        assert redeploy_receipt.status == 1

        return {
            "child_balance_after_redeploy": w3.eth.get_balance(child_addr),
            "validator_gained": w3.eth.get_balance(ADDRS["validator"])
            > validator_balance_before,
            "child_addr": child_addr,
        }

    with ThreadPoolExecutor(2) as pool:
        futs = [pool.submit(process, w3) for w3 in [ethermint.w3, geth.w3]]
        results = {name: f.result() for name, f in zip(["ethermint", "geth"], futs)}

    for name, r in results.items():
        assert r["child_balance_after_redeploy"] == 0, (
            f"{name}: redeployed child must have 0 balance"
            f"(child={r['child_addr']})."
        )
        assert not r[
            "validator_gained"
        ], f"{name}: validator must not gain funds from the recovery attempt."
```
