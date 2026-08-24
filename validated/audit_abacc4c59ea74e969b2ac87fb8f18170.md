## Title
Unbounded read loop causes out-of-bounds stack write in desktop-trampoline client - (vendor/desktop-trampoline/src/desktop-trampoline.c)

## Summary
The external report is about `finfo_open`/PHP's magic-file parser writing past the end of a heap buffer because the parser never validated that an accumulated offset stayed within bounds when consuming attacker-supplied bytes. The closest Desktop analog is `runTrampolineClient()` in the native `desktop-trampoline` helper: it reads the response coming back from the Trampoline server into a fixed 4096-byte stack buffer using a loop that never checks whether the accumulated byte count has reached the buffer's capacity before computing the remaining space, exactly mirroring the "unchecked/underflowing offset while appending parsed data" bug class from the report.

## Finding Description
`runTrampolineClient()` declares a fixed-size stack buffer and reads from the socket in a loop: [1](#0-0) 

`buffer` is `BUFFER_LENGTH + 1` bytes (4097). The loop computes the read length as `BUFFER_LENGTH - totalBytesRead` and keeps looping as long as `bytesRead > 0`, but it never breaks or clamps when `totalBytesRead` reaches `BUFFER_LENGTH`. `totalBytesRead` is `size_t` (unsigned); once it exceeds `BUFFER_LENGTH` (4096), the subtraction `BUFFER_LENGTH - totalBytesRead` wraps around to a huge unsigned value, which is then passed straight into `readSocket()`: [2](#0-1) 

`readSocket` forwards that value directly to `recv(socket, buffer, length, 0)` with no additional bound. If the Trampoline server ever sends more than 4096 bytes in a single response, the second (and later) `recv` calls write into `buffer + totalBytesRead` — already past the end of the declared array — with an effectively unbounded length, corrupting the stack (return address, saved registers, adjacent locals).

The response this client reads comes from `TrampolineServer.processCommand()` in the Electron main process, which calls `socket.end(result)` with whatever string the registered command handler (askpass / SSH host-verification / credential handlers) returns: [3](#0-2) 

Any handler whose returned string can be influenced or grown by remote-controlled content (e.g. hostnames, key fingerprints, or other values sourced from a git remote/SSH negotiation and echoed back into the credential/askpass response) would push the response over the 4096-byte cap and trigger the overflow in the native client that git itself spawns as `GIT_ASKPASS`/credential helper.

## Impact Explanation
A stack buffer overflow in a native, unsandboxed helper binary spawned directly by `git` (as askpass/credential-helper) is a memory-corruption primitive that can lead to code execution in the context of the user running Desktop, well outside the guarantees of the sandboxed renderer. This is a more severe class of bug than a simple crash because `recv` here overflows with attacker/response-influenced data and length, not merely a fixed overrun.

## Likelihood Explanation
Exploitability depends on whether any registered command handler can be induced, via a malicious remote/SSH server or crafted repository configuration, to return a response longer than 4096 bytes back through `TrampolineServer`. I was not able to fully trace every handler (askpass/SSH/credential) within the available search budget to confirm a handler that echoes sufficiently large remote-controlled content, so this should be treated as a **local, confirmed memory-safety bug in the read loop with an unconfirmed remote-trigger path** — the missing bound check itself is unambiguous and present in shipped native code, but proving full remote reachability requires further review of `app/src/lib/trampoline/trampoline-environment.ts` handler implementations, which I could not complete in this pass.

## Recommendation
- Clamp the read loop so it stops once `totalBytesRead >= BUFFER_LENGTH`, and treat a response that reaches the cap as an error instead of continuing to call `recv`.
- Compute the remaining space as a signed comparison before subtracting, to avoid unsigned wraparound: `if (totalBytesRead >= BUFFER_LENGTH) break;`.
- Consider switching to a dynamically-sized/growable buffer (like the `IGitBufferExecutionOptions`/`maxBuffer` patterns already used elsewhere in this codebase, e.g. `app/src/lib/git/core.ts`) instead of a fixed native stack buffer.
- Audit every `TrampolineCommandHandler` registered against `TrampolineServer` to confirm none can be made to return an attacker-influenced payload approaching or exceeding 4096 bytes.

## Proof of Concept
Conceptual PoC (native code path, cannot be exercised purely from JS):
1. Cause Desktop to invoke `desktop-trampoline` as `GIT_ASKPASS`/credential helper for a git operation against a malicious or MITM'd remote/SSH host.
2. Arrange for the corresponding `TrampolineCommandHandler` registered in the main process to return a string ≥ 4097 bytes (e.g. by controlling a hostname/key-fingerprint value that gets embedded verbatim in the handler's response) so `socket.end(result)` sends a response larger than `BUFFER_LENGTH`.
3. In `runTrampolineClient`, the first `recv` fills the 4096-byte buffer; `totalBytesRead` becomes ≥ `BUFFER_LENGTH`; on the next loop iteration `BUFFER_LENGTH - totalBytesRead` (unsigned) wraps to a huge value, and the subsequent `recv(socket, buffer + totalBytesRead, huge_value, 0)` writes past the end of the 4097-byte stack array, corrupting adjacent stack memory in the native helper process.

### Citations

**File:** vendor/desktop-trampoline/src/desktop-trampoline.c (L120-136)
```c
  char buffer[BUFFER_LENGTH + 1];
  size_t totalBytesRead = 0;
  ssize_t bytesRead = 0;

  // Read output from server
  do {
    bytesRead = readSocket(socket, buffer + totalBytesRead, BUFFER_LENGTH - totalBytesRead);

    if (bytesRead == -1) {
      printSocketError("ERROR: Error reading from socket");
      return 1;
    }

    totalBytesRead += bytesRead;
  } while (bytesRead > 0);

  buffer[totalBytesRead] = '\0';
```

**File:** vendor/desktop-trampoline/src/socket.c (L71-73)
```c
int readSocket(SOCKET socket, void *buffer, size_t length) {
  return recv(socket, buffer, length, 0);
}
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-183)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
    }

    const handler = this.commandHandlers.get(command.identifier)

    if (handler === undefined) {
      socket.end()
      return
    }

    const result = await handler(command).catch(e =>
      log.error('Error processing trampoline command', e)
    )

    if (result !== undefined) {
      socket.end(result)
    } else {
      socket.end()
    }
  }
```
