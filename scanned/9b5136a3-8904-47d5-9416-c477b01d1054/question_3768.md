# Q3768: GenDocProvider signature replay

## Question
Can an unprivileged attacker enter through submit an EIP-712 / signed transaction or signed Cosmos-EVM message through a public transaction path and use typed-data fields, domain separator fields, chain-id, signer bytes, address encodings, and replay conditions so that `server/start.go:GenDocProvider` mishandles signed-message binding path because `GenDocProvider` may omit nonce, expiry, chain binding, or message-class separation needed to keep one valid signature from authorizing repeated or shifted actions, causing `the one-time authorization state` and `the set of actions that the same signature can trigger` to diverge or settle in the wrong order, breaking the invariant that every accepted signature must authorize exactly one intended action under one bounded context and leading to `Theft / unauthorized extraction of funds`?

## Target
- File/function: `server/start.go:GenDocProvider`
- Entrypoint: submit an EIP-712 / signed transaction or signed Cosmos-EVM message through a public transaction path
- Attacker controls: typed-data fields, domain separator fields, chain-id, signer bytes, address encodings, and replay conditions
- Exploit idea: Drive the signed-message binding path through a crafted path that reaches `GenDocProvider` with attacker-controlled typed-data fields, domain separator fields, chain-id, signer bytes, address encodings, and replay conditions. Then force the failure, replay, nested-call, or ordering condition described above and compare `the one-time authorization state` against `the set of actions that the same signature can trigger`.
- Invariant to test: every accepted signature must authorize exactly one intended action under one bounded context
- Expected Immunefi impact: `Theft / unauthorized extraction of funds`
- Fast validation: reuse the same signature across altered context, replay timing, and message wrappers and assert only the intended one-time action succeeds
