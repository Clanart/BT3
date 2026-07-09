# Q2045: EVM ENearProxy mint shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` because `evm/src/eNear/contracts/ENearProxy.sol::mint` trusts the same response envelope under a different meaning, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
