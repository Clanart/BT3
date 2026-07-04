Looking at the vulnerability class — **error handling that silently swallows or misreports failures from external calls** — I need to find an analog in the StarkNet OS execution layer.

Let me examine the deploy syscall implementation and compare it to how `call_contract` handles failures.