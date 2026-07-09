# Q1241: EVM ENearProxy mint asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/eNear/contracts/ENearProxy.sol::mint` violate `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context` in the `asset-branch confusion on finalization` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
