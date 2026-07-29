[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/vm/keeper/state_transition.go (L1-29)
```go
package keeper

import (
	"fmt"
	"math/big"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core"
	"github.com/ethereum/go-ethereum/core/tracing"
	ethtypes "github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/core/vm"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/params"

	cmttypes "github.com/cometbft/cometbft/types"

	antetypes "github.com/cosmos/evm/ante/types"
	rpctypes "github.com/cosmos/evm/rpc/types"
	"github.com/cosmos/evm/utils"
	"github.com/cosmos/evm/x/vm/statedb"
	"github.com/cosmos/evm/x/vm/types"

	errorsmod "cosmossdk.io/errors"
	"cosmossdk.io/math"
	storetypes "cosmossdk.io/store/types"

	sdk "github.com/cosmos/cosmos-sdk/types"
	consensustypes "github.com/cosmos/cosmos-sdk/x/consensus/types"
)
```

**File:** x/vm/keeper/utils.go (L13-19)
```go
func (k *Keeper) IsContract(ctx sdk.Context, addr common.Address) bool {
	codeHash := k.GetCodeHash(ctx, addr)
	code := k.GetCode(ctx, codeHash)

	_, delegated := ethtypes.ParseDelegation(code)
	return len(code) > 0 && !delegated
}
```

**File:** ante/evm/06_account_verification.go (L32-42)
```go
	if account != nil && account.HasCodeHash() {
		// check eip-7702
		code := evmKeeper.GetCode(ctx, common.BytesToHash(account.CodeHash))
		_, delegated := ethtypes.ParseDelegation(code)
		if len(code) > 0 && !delegated {
			return errorsmod.Wrapf(
				errortypes.ErrInvalidType,
				"the sender is not EOA: address %s", from,
			)
		}
	}
```
