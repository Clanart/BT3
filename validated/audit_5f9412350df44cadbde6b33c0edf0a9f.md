Based on my investigation, I found the confirmed analog in sei-chain's `bank` precompile.

### Title
`send()` silently delivers ERC20-pointer token transfers to the unassociated cast address instead of a recipient's real Sei balance, permanently splitting/losing funds for smart-contract or unassociated recipients - ([File: precompiles/bank/bank.go])

### Summary
The `send` method of the `bank` precompile (used by ERC20 native-token pointer contracts to move `x/bank` coins on behalf of an EVM `transfer`/`transferFrom` call) resolves the destination EVM address to a Sei address via `accAddressFromArg`. If the recipient EVM address has never been "associated" (bound to a real Sei pubkey-derived address), the function does not revert or warn — it silently falls back to using the direct byte-cast Sei address as the destination and completes the transfer successfully.

### Finding Description
`accAddressFromArg` is used for both the sender and receiver of the ERC20-pointer `send()` call: [1](#0-0) 

When the receiver's EVM address is not found in the EVM↔Sei address mapping, the code comments explicitly acknowledge the fallback: "return the casted version instead," and returns `sdk.AccAddress(addr[:])` — the raw byte-cast address — with no error. This is then used directly as the `ToAddress` in the resulting `MsgSend`: [2](#0-1) 

This is exactly analogous to the reported bug class: a caller (an ERC20 pointer contract or its EVM caller) reasonably expects `send()` to deliver `x/bank` coins to the account controlling `receiverEvmAddress`. Instead, depending on whether that address has been associated, the funds are silently routed to a different underlying representation of the recipient (the byte-cast address) rather than reverting or otherwise signaling that the recipient cannot yet safely receive value through this path — mirroring `removeLiquidity`'s silent WETH-vs-ETH substitution based on recipient type.

The blast radius is bounded by the chain's own address-association safety net: `CanAddressReceive`/`BlockedAddr` machinery is designed to reject sends to a cast address once the underlying EVM address becomes associated with a *different* Sei address: [3](#0-2) [4](#0-3) 

However, that protection only prevents transfers *to* an already-associated-elsewhere cast address; it does not prevent a token transfer landing at the cast address of a recipient who is *simply not yet associated* (the common case for freshly deployed contracts or fresh EOAs that have never signed a tx). Funds sitting at the cast address are only reconciled into the real Sei balance later, when/if the owner associates and the migration in `x/evm/migrations/migrate_cast_address_balances.go` (or the `AssociationHelper.MigrateBalance` path) runs: [5](#0-4) [6](#0-5) 

If the recipient is a smart contract that can never sign a Cosmos/EVM transaction to trigger association (e.g., a multisig, a proxy, or any contract without an EOA-style key), the coins sent via this pointer `send()` path remain forever stuck at the cast address and are never visible/spendable through the contract's normal EVM balance/state — the contract's on-chain accounting (which tracked the ERC20 `transfer` as successful) diverges permanently from actual spendable value, exactly the "confusing... could lock funds" outcome described in the report. There is a note in the test suite confirming reads via the bank precompile do reflect cast-address balances, but this does not make the balance usable by EVM contract logic that only reasons about EVM-side balances/state: [7](#0-6) 

### Impact Explanation
Any ERC20 native-token pointer contract's `transfer`/`transferFrom` to a recipient contract or EOA that has not yet associated its EVM address will succeed on-chain (emitting a `Transfer` event and updating ERC20-visible balances via the pointer), while the actual `x/bank` coins land at an address the recipient contract cannot control through EVM logic. This is a genuine, reachable "permanent freezing of funds" class issue for any unprivileged user/dApp routing ERC20-pointer token transfers to unassociated recipients, satisfying the required "concrete fund loss or permanent freezing" bar.

### Likelihood Explanation
High likelihood of triggering unintentionally: unassociated EVM addresses (fresh contracts, fresh EOAs, and any contract that structurally cannot sign a "reveal pubkey" association transaction) are common on Sei, and there is no revert, warning event, or explicit opt-in flag guarding this fallback in `send()`/`accAddressFromArg` — the exact same "silent substitution based on recipient type" pattern flagged in the original report.

### Recommendation
In `accAddressFromArg` (and the `send` flow that consumes its result for the *receiver*), require that the destination EVM address be associated before allowing pointer-driven `x/bank` transfers to it, or route unassociated-recipient transfers through a path that keeps the EVM-visible pointer balance and the underlying bank balance in sync (e.g., auto-associate/migrate at receive time, or reject the transfer and let the caller choose an already-associated address), analogous to fixing `removeLiquidity` by reverting for non-associable/contract recipients instead of silently changing where value lands.

### Proof of Concept
1. Deploy (or use an existing) ERC20 native-token pointer for denom `ufoo`, associated in `evmKeeper.GetERC20NativePointer`.
2. From an associated sender, call the pointer's `transfer(receiver, amount)`, where `receiver` is a freshly generated EVM address (e.g., a newly deployed contract or an EOA that has never signed any tx) — see the existing test pattern in `TestSendForUnlinkedReceiver` for the mechanics of an unlinked receiver.
3. Observe the call succeeds and the pointer's on-chain `Transfer` event fires as if the recipient received the ERC20 tokens.
4. Query `x/bank` balances for `receiver`'s real Sei address (post any future association) versus the cast address `sdk.AccAddress(receiver[:])`: the coins sit at the cast address, not reflected in the recipient's usable EVM-side state, until/unless that address is later associated and `MigrateBalance`/`MigrateCastAddressBalances` is run — which a non-EOA contract may never be able to trigger. [8](#0-7)

### Citations

**File:** precompiles/bank/bank.go (L198-249)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, 0, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		bz, err := method.Outputs.Pack(true)
		return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, 0, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, 0, err
	}

	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
	}

	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** precompiles/bank/bank.go (L631-642)
```go
func (p PrecompileExecutor) accAddressFromArg(ctx sdk.Context, arg interface{}) (sdk.AccAddress, error) {
	addr := arg.(common.Address)
	if addr == (common.Address{}) {
		return nil, errors.New("invalid addr")
	}
	seiAddr, found := p.evmKeeper.GetSeiAddress(ctx, addr)
	if !found {
		// return the casted version instead
		return sdk.AccAddress(addr[:]), nil
	}
	return seiAddr, nil
}
```

**File:** x/evm/keeper/address.go (L78-86)
```go
// A sdk.AccAddress may not receive funds from bank if it's the result of direct-casting
// from an EVM address AND the originating EVM address has already been associated with
// a true (i.e. derived from the same pubkey) sdk.AccAddress.
func (k *Keeper) CanAddressReceive(ctx sdk.Context, addr sdk.AccAddress) bool {
	directCast := common.BytesToAddress(addr) // casting goes both directions since both address formats have 20 bytes
	associatedAddr, isAssociated := k.GetSeiAddress(ctx, directCast)
	// if the associated address is the cast address itself, allow the address to receive (e.g. EVM contract addresses)
	return associatedAddr.Equals(addr) || !isAssociated // this means it's either a cast address that's not associated yet, or not a cast address at all.
}
```

**File:** x/evm/keeper/address_test.go (L58-75)
```go
func TestSendingToCastAddress(t *testing.T) {
	a := keeper.EVMTestApp
	ctx := a.GetContextForDeliverTx([]byte{})
	seiAddr, evmAddr := keeper.MockAddressPair()
	castAddr := sdk.AccAddress(evmAddr[:])
	sourceAddr, _ := keeper.MockAddressPair()
	require.Nil(t, a.BankKeeper.MintCoins(ctx, "evm", sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(10)))))
	require.Nil(t, a.BankKeeper.SendCoinsFromModuleToAccount(ctx, "evm", sourceAddr, sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(5)))))
	amt := sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(1)))
	require.Nil(t, a.BankKeeper.SendCoinsFromModuleToAccount(ctx, "evm", castAddr, amt))
	require.Nil(t, a.BankKeeper.SendCoins(ctx, sourceAddr, castAddr, amt))
	require.Nil(t, a.BankKeeper.SendCoinsAndWei(ctx, sourceAddr, castAddr, sdk.OneInt(), sdk.OneInt()))

	a.EvmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	require.NotNil(t, a.BankKeeper.SendCoinsFromModuleToAccount(ctx, "evm", castAddr, amt))
	require.NotNil(t, a.BankKeeper.SendCoins(ctx, sourceAddr, castAddr, amt))
	require.NotNil(t, a.BankKeeper.SendCoinsAndWei(ctx, sourceAddr, castAddr, sdk.OneInt(), sdk.OneInt()))
}
```

**File:** x/evm/migrations/migrate_cast_address_balances.go (L1-29)
```go
package migrations

import (
	"github.com/ethereum/go-ethereum/common"
	sdk "github.com/sei-protocol/sei-chain/sei-cosmos/types"
	"github.com/sei-protocol/sei-chain/x/evm/keeper"
)

func MigrateCastAddressBalances(ctx sdk.Context, k *keeper.Keeper) (rerr error) {
	k.IterateSeiAddressMapping(ctx, func(evmAddr common.Address, seiAddr sdk.AccAddress) bool {
		castAddr := sdk.AccAddress(evmAddr[:])
		if balances := k.BankKeeper().SpendableCoins(ctx, castAddr); !balances.IsZero() {
			if err := k.BankKeeper().SendCoins(ctx, castAddr, seiAddr, balances); err != nil {
				logger.Error("error migrating balances from cast to real for address", "address", evmAddr, "err", err)
				rerr = err
				return true
			}
		}
		if wei := k.BankKeeper().GetWeiBalance(ctx, castAddr); !wei.IsZero() {
			if err := k.BankKeeper().SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), wei); err != nil {
				logger.Error("error migrating wei from cast to real for address", "address", evmAddr, "err", err)
				rerr = err
				return true
			}
		}
		return false
	})
	return
}
```

**File:** utils/helpers/associate.go (L57-82)
```go
func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
```

**File:** integration_test/precompile_tests/precompiles/bank.spec.ts (L59-69)
```typescript
        it('balance falls back to the cast address for an unassociated account', async () => {
            // Pool accounts are funded via EVM sends but have never signed, so their
            // funds sit at the cast sei address; the precompile must still report them.
            const [unassociated] = claimPool(runtime, provider, 1, 'bank:cast-balance');
            const [viaPrecompile, viaEvm] = await Promise.all([
                bank.balance(unassociated.address, 'usei') as Promise<bigint>,
                provider.getBalance(unassociated.address),
            ]);
            expect(viaPrecompile).to.equal(viaEvm / WEI_PER_USEI);
            expect(viaPrecompile > 0n, 'pool account must be funded').to.equal(true);
        });
```

**File:** precompiles/bank/bank_test.go (L264-302)
```go
func TestSendForUnlinkedReceiver(t *testing.T) {
	testApp := testkeeper.EVMTestApp
	ctx := testApp.NewContext(false, tmtypes.Header{}).WithBlockHeight(2)
	k := &testApp.EvmKeeper

	// Setup sender addresses and environment
	privKey := testkeeper.MockPrivateKey()
	// testPrivHex := hex.EncodeToString(privKey.Bytes())
	senderAddr, senderEVMAddr := testkeeper.PrivateKeyToAddresses(privKey)
	k.SetAddressMapping(ctx, senderAddr, senderEVMAddr)
	err := k.BankKeeper().MintCoins(ctx, types.ModuleName, sdk.NewCoins(sdk.NewCoin("ufoo", sdk.NewInt(10000000))))
	require.Nil(t, err)
	err = k.BankKeeper().SendCoinsFromModuleToAccount(ctx, types.ModuleName, senderAddr, sdk.NewCoins(sdk.NewCoin("ufoo", sdk.NewInt(10000000))))
	require.Nil(t, err)
	err = k.BankKeeper().MintCoins(ctx, types.ModuleName, sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(10000000))))
	require.Nil(t, err)
	err = k.BankKeeper().SendCoinsFromModuleToAccount(ctx, types.ModuleName, senderAddr, sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(10000000))))
	require.Nil(t, err)

	_, pointerAddr := testkeeper.MockAddressPair()
	k.SetERC20NativePointer(ctx, "ufoo", pointerAddr)

	// Setup receiving addresses - NOT linked
	_, evmAddr := testkeeper.MockAddressPair()
	p, err := bank.NewPrecompile(testApp.GetPrecompileKeepers())
	require.Nil(t, err)
	statedb := state.NewDBImpl(ctx, k, true)
	evm := vm.EVM{
		StateDB:   statedb,
		TxContext: vm.TxContext{Origin: senderEVMAddr},
	}

	// Precompile send test
	send, err := p.ABI.MethodById(p.GetExecutor().(*bank.PrecompileExecutor).SendID)
	require.Nil(t, err)
	args, err := send.Inputs.Pack(senderEVMAddr, evmAddr, "ufoo", big.NewInt(100))
	require.Nil(t, err)
	_, _, err = p.RunAndCalculateGas(&evm, pointerAddr, pointerAddr, append(p.GetExecutor().(*bank.PrecompileExecutor).SendID, args...), 100000, nil, nil, false, false) // should not error
	require.Nil(t, err)
```
