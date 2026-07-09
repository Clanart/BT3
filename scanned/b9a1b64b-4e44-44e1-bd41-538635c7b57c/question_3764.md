# Q3764: EVM OmniBridge deployToken address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public EVM `deployToken(bytes,MetadataPayload)`` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` normalizes two distinct chain-specific addresses into the same local representation, violating `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
