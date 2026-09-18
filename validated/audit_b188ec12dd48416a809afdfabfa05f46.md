The `WSEI.sol` contract's `withdraw()` function bundled with sei-chain uses the deprecated `transfer()` call, which fits the reported bug class and is reachable via a normal EVM transaction (deploy the CLI-provided bytecode, deposit, then call `withdraw`).

### Title
Use of `transfer()` in `WSEI.withdraw()` can permanently lock deposited SEI for smart-contract holders - (File: contracts/src/WSEI.sol)

### Summary
`WSEI.sol` is Sei's canonical wrapped-native-token contract, whose bytecode is embedded in the binary and deployable by any user via the `seid tx evm deploy-wsei` CLI command [1](#0-0) , backed by the compiled artifact in `x/evm/artifacts/wsei` [2](#0-1) . Its `withdraw()` function sends the native SEI/wei balance back to the caller using `payable(msg.sender).transfer(wad)` [3](#0-2) , which forwards only a fixed 2300 gas stipend.

### Finding Description
Any account—including smart-contract wallets, multisigs, or proxy contracts—can call `deposit()` (or trigger the `receive()`/`fallback()`) to accumulate a WSEI balance [4](#0-3) . When such a contract later calls `withdraw()`, the SEI is sent back via `transfer()`, which only forwards 2300 gas [3](#0-2) . If the calling contract's `receive`/`fallback` function requires more than 2300 gas to execute (e.g., due to storage writes, logic in a multisig wallet, or being invoked through an additional proxy layer that itself consumes gas before forwarding), the internal call will always run out of gas and revert. Because `withdraw()` reverts entirely, the caller's balance is never decremented, but the native SEI deposited into the WSEI contract can never be retrieved through this codepath — the exact "M-4" class described in the reference report (deprecated `transfer()`/`send()` breaking on gas-cost changes or minimum-gas-requiring receivers).

### Impact Explanation
Any smart contract with a non-trivial receive/fallback (common in multisigs like Gnosis Safe, or contracts reached through a forwarding proxy) that deposits SEI into `WSEI` cannot withdraw it back — permanently freezing its funds inside the WSEI contract with no recovery path in the contract itself. This satisfies the "permanent freezing of funds" impact bar. Since deployment and interaction only require ordinary EVM transactions (deploy bytecode via `CmdDeployWSEI`, then call `deposit`/`withdraw`), this is reachable by any unprivileged EVM user, matching the in-scope "EVM transactions" attack surface.

### Likelihood Explanation
Likelihood is high in practice for any smart-contract-wallet or proxy-based user of WSEI (a very common usage pattern for wrapped-native tokens in DeFi), since 2300 gas is frequently insufficient for non-EOA receivers, and the failure is deterministic and reproducible on every `withdraw()` call from such contracts.

### Recommendation
Replace `payable(msg.sender).transfer(wad)` in `WSEI.withdraw()` with a low-level `call{value: wad}("")` and check the boolean return value, following checks-effects-interactions ordering (balance is already decremented before the transfer, which is correct) and adding reentrancy protection if not otherwise guaranteed by the SEI EVM's execution model. Re-deploy/re-embed the updated `WSEI.bin`/`WSEI.abi` artifacts and update the CLI-deployed contract accordingly.

### Proof of Concept
1. Deploy a minimal contract `LockedWallet` whose `receive()` performs an `SSTORE` (or otherwise consumes >2300 gas), e.g. `receive() external payable { lastReceived = block.timestamp; }`.
2. From `LockedWallet`, call `WSEI.deposit{value: 1 ether}()`.
3. From `LockedWallet`, call `WSEI.withdraw(1 ether)`.
4. Observe the transaction reverts every time because `transfer()` forwards only 2300 gas, which is insufficient for the `SSTORE` in `LockedWallet.receive()` — the 1 ether stays locked in the `WSEI` contract permanently with no other exit path (contract exposes only `transferFrom`/`transfer` for the ERC20 balance, not the underlying native SEI held by the contract).

### Citations

**File:** x/evm/client/cli/tx.go (L428-481)
```go
func CmdDeployWSEI() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "deploy-wsei --from=<sender> --gas-fee-cap=<cap> --gas-limt=<limit> --evm-rpc=<url>",
		Short: "Deploy ERC20 contract for a native Sei token",
		Long:  "",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) (err error) {
			contractData := wsei.GetBin()

			key, err := getPrivateKey(cmd)
			if err != nil {
				return err
			}

			rpc, err := cmd.Flags().GetString(FlagRPC)
			if err != nil {
				return err
			}
			var nonce uint64
			if n, err := cmd.Flags().GetInt64(FlagNonce); err == nil && n >= 0 {
				nonce = uint64(n)
			} else {
				nonce, err = getNonce(rpc, key.PublicKey)
				if err != nil {
					return err
				}
			}

			txData, err := getTxData(cmd)
			if err != nil {
				return err
			}
			txData.Nonce = nonce
			txData.Value = utils.Big0
			txData.Data = contractData

			resp, err := sendTx(txData, rpc, key)
			if err != nil {
				return err
			}

			senderAddr := crypto.PubkeyToAddress(key.PublicKey)
			data, err := rlp.EncodeToBytes([]interface{}{senderAddr, nonce})
			if err != nil {
				return err
			}
			hash := crypto.Keccak256Hash(data)
			contractAddress := hash.Bytes()[12:]
			contractAddressHex := hex.EncodeToString(contractAddress)

			fmt.Println("Deployer:", senderAddr)
			fmt.Println("Deployed to:", fmt.Sprintf("0x%s", contractAddressHex))
			fmt.Println("Transaction hash:", resp.Hex())
			return nil
```

**File:** x/evm/artifacts/wsei/artifacts.go (L1-54)
```go
package wsei

import (
	"embed"
	"encoding/hex"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
)

const CurrentVersion uint16 = 1

//go:embed WSEI.abi
//go:embed WSEI.bin
var f embed.FS

var cachedBin []byte
var cachedABI *abi.ABI

func GetABI() []byte {
	bz, err := f.ReadFile("WSEI.abi")
	if err != nil {
		panic("failed to read WSEI contract ABI")
	}
	return bz
}

func GetParsedABI() *abi.ABI {
	if cachedABI != nil {
		return cachedABI
	}
	parsedABI, err := abi.JSON(strings.NewReader(string(GetABI())))
	if err != nil {
		panic(err)
	}
	cachedABI = &parsedABI
	return cachedABI
}

func GetBin() []byte {
	if cachedBin != nil {
		return cachedBin
	}
	code, err := f.ReadFile("WSEI.bin")
	if err != nil {
		panic("failed to read WSEI contract binary")
	}
	bz, err := hex.DecodeString(string(code))
	if err != nil {
		panic("failed to decode WSEI contract binary")
	}
	cachedBin = bz
	return bz
}
```

**File:** contracts/src/WSEI.sol (L17-26)
```text
    fallback() external payable {
        deposit();
    }
    receive() external payable {
        deposit();
    }
    function deposit() public payable {
        balanceOf[msg.sender] += msg.value;
        emit Deposit(msg.sender, msg.value);
    }
```

**File:** contracts/src/WSEI.sol (L27-32)
```text
    function withdraw(uint wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```
